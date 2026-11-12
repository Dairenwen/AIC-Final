"""会议/期刊画像工具 — 获取详情：级别、审稿周期、接收率、版面费等"""
from typing import Optional
from knowledge import get_knowledge_base
from utils.logger import get_logger

logger = get_logger(__name__)


async def get_venue_profile(venue_id: str) -> Optional[dict]:
    kb = get_knowledge_base()
    venue = kb.get_by_id(venue_id)
    if not venue:
        return None
    return {
        "venue_id": venue["abbreviation"], "type": venue["type"],
        "abbreviation": venue["abbreviation"], "full_name": venue["full_name"],
        "ccf_level": venue.get("ccf_level", ""), "caai_level": venue.get("caai_level", ""),
        "research_areas": venue.get("research_areas", []),
        "acceptance_rate": venue.get("acceptance_rate", 0),
        "avg_review_weeks": venue.get("avg_review_weeks", 0),
        "review_model": venue.get("review_model", ""),
        "publication_fee": venue.get("publication_fee", 0),
        "is_oa": venue.get("is_oa", False),
        "next_deadline": venue.get("next_deadline", ""),
        "notification_date": venue.get("notification_date", ""),
    }


async def batch_get_venue_profiles(venue_ids: list[str]) -> dict[str, dict]:
    profiles = {}
    for vid in venue_ids:
        profile = await get_venue_profile(vid)
        if profile:
            profiles[vid] = profile
    logger.info(f"批量获取 {len(profiles)}/{len(venue_ids)} 个 venue 画像")
    return profiles
