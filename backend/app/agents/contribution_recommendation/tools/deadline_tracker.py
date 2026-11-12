"""Deadline 查询工具 — 截稿日期、通知日期、到期提醒"""
from datetime import datetime
from typing import Optional
from utils.logger import get_logger

logger = get_logger(__name__)


async def track_deadlines(candidate_venues: list[dict],
                           reminder_days: list[int] = [60, 30, 14, 7]) -> dict:
    today = datetime.now()
    deadline_list = []
    urgent_list = []

    for v in candidate_venues:
        dl_str = v.get("next_deadline", "")
        if dl_str == "N/A (rolling)" or not dl_str:
            deadline_list.append({"venue": v["abbreviation"], "type": v["type"],
                                  "deadline": "滚动审稿，无固定截稿日",
                                  "days_remaining": None, "urgency": "none", "reminders": []})
            continue
        try:
            dl_date = datetime.strptime(dl_str, "%Y-%m-%d")
            days_left = (dl_date - today).days
            reminders = [{"days": rd, "triggered": True,
                          "message": f"距 {v['abbreviation']} 截稿仅剩 {days_left} 天！"}
                         for rd in reminder_days if 0 < days_left <= rd]
            urgency = "expired" if days_left < 0 else ("urgent" if days_left <= 30 else
                      ("warning" if days_left <= 60 else "normal"))
            entry = {"venue": v["abbreviation"], "full_name": v.get("full_name", ""),
                     "type": v["type"], "deadline": dl_str,
                     "notification_date": v.get("notification_date", ""),
                     "days_remaining": max(days_left, 0), "urgency": urgency, "reminders": reminders}
            deadline_list.append(entry)
            if urgency in ("urgent", "expired"):
                urgent_list.append(entry)
        except ValueError as e:
            logger.warning(f"日期解析失败 {v['abbreviation']}: {e}")

    deadline_list.sort(key=lambda x: (0 if x["urgency"] == "expired" else
                                      1 if x["urgency"] == "urgent" else
                                      2 if x["urgency"] == "warning" else
                                      3 if x["urgency"] == "normal" else 4))
    logger.info(f"Deadline 查询完成: {len(urgent_list)} 紧急, 共 {len(deadline_list)}")
    return {"deadlines": deadline_list, "urgent_count": len(urgent_list),
            "urgent_venues": [u["venue"] for u in urgent_list],
            "next_30_days": [d for d in deadline_list if d.get("days_remaining") is not None and d.get("days_remaining", 999) <= 30],
            "generated_at": today.isoformat()}
