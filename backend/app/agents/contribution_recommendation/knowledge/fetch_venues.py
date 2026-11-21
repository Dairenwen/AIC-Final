"""
Venue 自动抓取与数据补全脚本

三阶段流水线:
  1. 从 GitHub API / 本地缓存 拉取 CCF 推荐列表(名称、级别、领域)
  2. 从 WikiCFP 抓取即将到来的截稿日期(可跳过)
  3. 用内置统计数据补全接收率、审稿周期等字段

用法:
  python knowledge/fetch_venues.py              # 运行全部
  python knowledge/fetch_venues.py --force       # 强制覆盖已有字段
  python knowledge/fetch_venues.py --dry-run     # 仅显示变更
  python knowledge/fetch_venues.py --no-deadlines # 跳过 WikiCFP
  python knowledge/fetch_venues.py --local CCF.json  # 使用本地CCF文件
"""

import argparse
import copy
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────────

# CCF 数据源(按优先级尝试)
CCF_SOURCES = [
    # GitHub API(比 raw 更容易通过防火墙)
    "https://api.github.com/repos/GroundbreakerLhy/CCF-Rank/contents/src/data/ccf-conferences.json",
    # Gitee 镜像(GitHub不可用时)
    "https://gitee.com/api/v5/repos/mirrors_groundbreaker/CCF-Rank/contents/src/data/ccf-conferences.json",
]

WIKICFP_SEARCH = "http://www.wikicfp.com/cfp/servlet/tool.search"
WIKICFP_EVENT = "http://www.wikicfp.com/cfp/servlet/tool.viewcfp?eventid={}"

OUTPUT_FILE = Path(__file__).parent / "fetched_venues.py"
CACHE_DIR = Path(__file__).parent / ".fetch_cache"
CACHE_CCF = CACHE_DIR / "ccf_list.json"

# ═══════════════════════════════════════════════════════════
# 内置统计数据(接收率、审稿周期等)
# ═══════════════════════════════════════════════════════════

KNOWN_STATS = {
    # ── CCF-A 会议 ──
    "AAAI": {"accept": 0.235, "weeks": 9, "review": "Double-blind"},
    "NeurIPS": {"accept": 0.258, "weeks": 11, "review": "Double-blind"},
    "ICML": {"accept": 0.275, "weeks": 10, "review": "Double-blind"},
    "IJCAI": {"accept": 0.21, "weeks": 8, "review": "Double-blind"},
    "ICLR": {"accept": 0.31, "weeks": 8, "review": "Double-blind"},
    "CVPR": {"accept": 0.262, "weeks": 8, "review": "Double-blind"},
    "ICCV": {"accept": 0.245, "weeks": 9, "review": "Double-blind"},
    "ACL": {"accept": 0.232, "weeks": 9, "review": "Double-blind"},
    "SIGIR": {"accept": 0.20, "weeks": 9, "review": "Double-blind"},
    "KDD": {"accept": 0.18, "weeks": 9, "review": "Double-blind"},
    "WWW": {"accept": 0.20, "weeks": 8, "review": "Double-blind"},
    "MM": {"accept": 0.24, "weeks": 8, "review": "Double-blind"},
    "SIGMOD": {"accept": 0.25, "weeks": 10, "review": "Double-blind"},
    "VLDB": {"accept": 0.194, "weeks": 12, "review": "Double-blind"},
    "ICDE": {"accept": 0.20, "weeks": 10, "review": "Single-blind"},
    "SIGGRAPH": {"accept": 0.27, "weeks": 9, "review": "Double-blind"},
    "STOC": {"accept": 0.30, "weeks": 12, "review": "Single-blind"},
    "FOCS": {"accept": 0.29, "weeks": 12, "review": "Single-blind"},
    "OSDI": {"accept": 0.18, "weeks": 8, "review": "Single-blind"},
    "SOSP": {"accept": 0.17, "weeks": 8, "review": "Single-blind"},
    "MobiCom": {"accept": 0.18, "weeks": 8, "review": "Double-blind"},
    "INFOCOM": {"accept": 0.195, "weeks": 8, "review": "Double-blind"},
    "MICRO": {"accept": 0.22, "weeks": 8, "review": "Double-blind"},
    "HPCA": {"accept": 0.23, "weeks": 8, "review": "Double-blind"},
    "ISCA": {"accept": 0.21, "weeks": 8, "review": "Double-blind"},
    "SC": {"accept": 0.24, "weeks": 8, "review": "Double-blind"},
    "CCS": {"accept": 0.19, "weeks": 8, "review": "Double-blind"},
    "S&P": {"accept": 0.13, "weeks": 8, "review": "Double-blind"},
    "USENIX Security": {"accept": 0.15, "weeks": 8, "review": "Double-blind"},
    "NDSS": {"accept": 0.20, "weeks": 8, "review": "Double-blind"},
    "CHI": {"accept": 0.24, "weeks": 8, "review": "Double-blind"},
    "ICSE": {"accept": 0.21, "weeks": 8, "review": "Double-blind"},
    "FSE": {"accept": 0.24, "weeks": 8, "review": "Double-blind"},
    "ASE": {"accept": 0.26, "weeks": 8, "review": "Double-blind"},
    "PLDI": {"accept": 0.22, "weeks": 8, "review": "Double-blind"},
    "POPL": {"accept": 0.23, "weeks": 10, "review": "Double-blind"},
    "ASPLOS": {"accept": 0.21, "weeks": 8, "review": "Double-blind"},
    # ── CCF-B 会议 ──
    "EMNLP": {"accept": 0.286, "weeks": 8, "review": "Double-blind"},
    "ECCV": {"accept": 0.295, "weeks": 8, "review": "Double-blind"},
    "NAACL": {"accept": 0.30, "weeks": 8, "review": "Double-blind"},
    "COLING": {"accept": 0.28, "weeks": 8, "review": "Double-blind"},
    "ICDM": {"accept": 0.195, "weeks": 8, "review": "Double-blind"},
    "CIKM": {"accept": 0.22, "weeks": 8, "review": "Double-blind"},
    "AAMAS": {"accept": 0.207, "weeks": 10, "review": "Double-blind"},
    "UAI": {"accept": 0.25, "weeks": 8, "review": "Double-blind"},
    "ECAI": {"accept": 0.25, "weeks": 8, "review": "Double-blind"},
    "ICRA": {"accept": 0.38, "weeks": 10, "review": "Single-blind"},
    "CoNLL": {"accept": 0.28, "weeks": 8, "review": "Double-blind"},
    "EACL": {"accept": 0.25, "weeks": 8, "review": "Double-blind"},
    "WSDM": {"accept": 0.20, "weeks": 8, "review": "Double-blind"},
    "ICASSP": {"accept": 0.35, "weeks": 8, "review": "Double-blind"},
    "MICCAI": {"accept": 0.31, "weeks": 8, "review": "Double-blind"},
    "ECML-PKDD": {"accept": 0.25, "weeks": 8, "review": "Double-blind"},
    "SDM": {"accept": 0.23, "weeks": 8, "review": "Double-blind"},
    "RecSys": {"accept": 0.24, "weeks": 8, "review": "Double-blind"},
    # ── CCF-C 会议 ──
    "BMVC": {"accept": 0.32, "weeks": 7, "review": "Double-blind"},
    "ACCV": {"accept": 0.33, "weeks": 8, "review": "Double-blind"},
    "ICANN": {"accept": 0.38, "weeks": 6, "review": "Double-blind"},
    "ICPR": {"accept": 0.42, "weeks": 6, "review": "Double-blind"},
    # ── CCF-A 期刊 ──
    "TPAMI": {"accept": 0.12, "weeks": 20, "review": "Single-blind"},
    "IJCV": {"accept": 0.15, "weeks": 16, "review": "Single-blind"},
    "JMLR": {"accept": 0.18, "weeks": 18, "review": "Single-blind"},
    "AIJ": {"accept": 0.15, "weeks": 36, "review": "Single-blind"},
    "TKDE": {"accept": 0.18, "weeks": 24, "review": "Single-blind"},
    "CSUR": {"accept": 0.10, "weeks": 16, "review": "Single-blind"},
    "TVCG": {"accept": 0.22, "weeks": 16, "review": "Single-blind"},
    "TIP": {"accept": 0.20, "weeks": 16, "review": "Single-blind"},
    "TOIS": {"accept": 0.18, "weeks": 14, "review": "Single-blind"},
    "TODS": {"accept": 0.15, "weeks": 14, "review": "Single-blind"},
    "TOCS": {"accept": 0.16, "weeks": 16, "review": "Single-blind"},
    "TCAD": {"accept": 0.25, "weeks": 14, "review": "Single-blind"},
    # ── CCF-B 期刊 ──
    "TNNLS": {"accept": 0.16, "weeks": 14, "review": "Single-blind"},
    "PR": {"accept": 0.22, "weeks": 12, "review": "Single-blind"},
    "TACL": {"accept": 0.20, "weeks": 12, "review": "Double-blind"},
    "TIFS": {"accept": 0.22, "weeks": 16, "review": "Single-blind"},
    "TASLP": {"accept": 0.25, "weeks": 14, "review": "Single-blind"},
    "JCST": {"accept": 0.25, "weeks": 12, "review": "Single-blind"},
    "SCIS": {"accept": 0.20, "weeks": 12, "review": "Single-blind"},
}

J_DEFAULTS = {"review_model": "Single-blind", "is_oa": False,
              "next_deadline": "N/A (rolling)", "notification_date": "N/A", "publication_fee": 0}
C_DEFAULTS = {"review_model": "Double-blind", "is_oa": False, "publication_fee": 0}

# CCF 级别 → 领域推测表(当 JSON 中无 research_areas 时使用)
CCF_AREA_GUESS = {
    "AAAI": ["人工智能", "机器学习"],
    "IJCAI": ["人工智能", "知识表示"],
    "NeurIPS": ["机器学习", "深度学习", "神经网络"],
    "ICML": ["机器学习", "深度学习", "统计学习"],
    "ICLR": ["深度学习", "表示学习", "生成模型"],
    "CVPR": ["计算机视觉", "图像识别", "目标检测"],
    "ICCV": ["计算机视觉", "图像理解", "3D重建"],
    "ACL": ["自然语言处理", "计算语言学"],
    "SIGIR": ["信息检索", "搜索", "推荐系统"],
    "KDD": ["数据挖掘", "知识发现", "大数据"],
    "WWW": ["Web技术", "信息检索", "社会网络"],
    "MM": ["多媒体", "计算机视觉", "音频处理"],
    "SIGMOD": ["数据库", "数据管理", "大数据"],
    "VLDB": ["数据库", "大数据"],
    "ICDE": ["数据库", "数据工程"],
    "SIGGRAPH": ["计算机图形学", "可视化"],
    "STOC": ["理论计算机科学", "算法"],
    "FOCS": ["理论计算机科学", "算法"],
    "OSDI": ["操作系统", "分布式系统"],
    "SOSP": ["操作系统", "分布式系统"],
    "MobiCom": ["计算机网络", "移动计算"],
    "INFOCOM": ["计算机网络"],
    "MICRO": ["计算机体系结构"],
    "HPCA": ["计算机体系结构"],
    "ISCA": ["计算机体系结构"],
    "SC": ["高性能计算"],
    "CCS": ["信息安全"],
    "S&P": ["信息安全"],
    "USENIX Security": ["信息安全"],
    "NDSS": ["信息安全"],
    "CHI": ["人机交互"],
    "ICSE": ["软件工程"],
    "FSE": ["软件工程"],
    "ASE": ["软件工程"],
    "PLDI": ["编程语言", "编译技术"],
    "POPL": ["编程语言", "形式化方法"],
    "ASPLOS": ["计算机体系结构", "操作系统"],
    "EMNLP": ["自然语言处理", "文本挖掘"],
    "ECCV": ["计算机视觉", "图像处理"],
    "NAACL": ["自然语言处理", "计算语言学"],
    "COLING": ["计算语言学", "自然语言处理"],
    "ICDM": ["数据挖掘", "知识发现"],
    "CIKM": ["信息检索", "知识管理", "数据挖掘"],
    "AAMAS": ["多智能体", "人工智能"],
    "UAI": ["机器学习", "概率方法", "贝叶斯"],
    "ECAI": ["人工智能", "知识表示"],
    "ICRA": ["机器人", "自动控制"],
    "CoNLL": ["自然语言处理", "机器学习"],
    "EACL": ["自然语言处理", "计算语言学"],
    "WSDM": ["信息检索", "数据挖掘", "推荐系统"],
    "ICASSP": ["语音处理", "信号处理", "深度学习"],
    "MICCAI": ["医学图像", "计算机视觉", "深度学习"],
    "ECML-PKDD": ["机器学习", "数据挖掘"],
    "SDM": ["数据挖掘", "机器学习"],
    "RecSys": ["推荐系统", "机器学习"],
    "BMVC": ["计算机视觉", "模式识别"],
    "ACCV": ["计算机视觉", "图像识别"],
    "ICANN": ["神经网络", "深度学习"],
    "ICPR": ["模式识别", "计算机视觉", "图像处理"],
    "TPAMI": ["计算机视觉", "模式识别", "机器学习"],
    "IJCV": ["计算机视觉", "图像处理"],
    "JMLR": ["机器学习", "统计学习"],
    "AIJ": ["人工智能", "知识表示"],
    "TKDE": ["数据挖掘", "数据库", "知识工程"],
    "CSUR": ["计算机科学"],
    "TVCG": ["可视化", "计算机图形学"],
    "TIP": ["图像处理", "计算机视觉"],
    "TOIS": ["信息检索", "信息系统"],
    "TODS": ["数据库"],
    "TOCS": ["计算机体系结构", "操作系统"],
    "TCAD": ["计算机体系结构", "EDA"],
    "TNNLS": ["神经网络", "深度学习"],
    "PR": ["模式识别", "计算机视觉"],
    "TACL": ["自然语言处理", "计算语言学"],
    "TIFS": ["信息安全"],
    "TASLP": ["语音处理", "自然语言处理"],
    "JCST": ["计算机科学"],
    "SCIS": ["计算机科学", "人工智能"],
}


class VenueFetcher:
    """三阶段 venue 数据抓取与补全"""

    def __init__(self, force: bool = False, no_deadlines: bool = False,
                 local_ccf: Optional[str] = None):
        self.force = force
        self.no_deadlines = no_deadlines
        self.local_ccf = local_ccf
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "VenueFetcher/1.0 (submission-recommend-agent)",
        })
        self.stats = {"ccf_fetched": 0, "deadlines_fetched": 0,
                      "new_venues": 0, "updated_fields": 0, "unchanged": 0}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════
    # Tier 1: CCF 推荐列表
    # ═══════════════════════════════════════════════════════

    def fetch_ccf_list(self) -> list[dict]:
        """获取 CCF 推荐列表: 本地文件 > GitHub API > Gitee API > 缓存"""
        logger.info("[Tier 1] 获取 CCF 推荐列表...")

        raw_data = None

        # 优先级 1: 用户指定的本地文件
        if self.local_ccf:
            path = Path(self.local_ccf)
            if path.exists():
                logger.info(f"  使用本地 CCF 文件: {path}")
                raw_data = json.loads(path.read_text(encoding="utf-8"))

        # 优先级 2: 在线 API
        if not raw_data:
            for url in CCF_SOURCES:
                try:
                    logger.info(f"  尝试: {url[:60]}...")
                    resp = self.session.get(url, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    # GitHub API 把文件内容编码在 content 字段(base64)
                    if "content" in data and data.get("encoding") == "base64":
                        import base64
                        raw_data = json.loads(base64.b64decode(data["content"]))
                    else:
                        raw_data = data
                    logger.info(f"  在线获取成功")
                    # 写入缓存
                    CACHE_CCF.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
                    break
                except Exception as e:
                    logger.warning(f"  失败: {e}")
                    continue

        # 优先级 3: 本地缓存
        if not raw_data and CACHE_CCF.exists():
            logger.info("  使用本地缓存")
            raw_data = json.loads(CACHE_CCF.read_text(encoding="utf-8"))

        if not raw_data:
            logger.warning("  CCF 数据获取失败，Tier 1 无产出")
            return []

        venues = self._parse_ccf_json(raw_data)
        self.stats["ccf_fetched"] = len(venues)
        logger.info(f"  CCF 解析完成: {len(venues)} 个 venue")
        return venues

    # CCF 子领域 → 研究领域关键词映射
    CATEGORY_AREA_MAP = {
        "计算机体系结构": ["计算机体系结构", "并行计算", "存储系统"],
        "计算机网络": ["计算机网络", "网络通信"],
        "网络与信息安全": ["信息安全", "网络安全"],
        "人工智能": ["人工智能", "机器学习", "深度学习"],
        "计算机图形学与多媒体": ["计算机图形学", "多媒体", "可视化"],
        "软件工程": ["软件工程", "程序设计"],
        "数据库": ["数据库", "数据挖掘", "信息检索"],
        "人机交互与普适计算": ["人机交互", "普适计算"],
        "计算机科学理论": ["理论计算机科学", "算法"],
        "交叉": ["交叉学科", "人工智能"],
    }

    def _parse_ccf_json(self, raw) -> list[dict]:
        """解析 CCF JSON (格式: {conferences:[{abbr,fullName,rank,category}], journals:[...]})"""
        venues = []
        seen = set()

        # 确定数据列表
        if isinstance(raw, list):
            items = raw
            vtype = "conference"
        elif isinstance(raw, dict) and "conferences" in raw:
            items = [(item, "conference") for item in raw.get("conferences", [])]
            items += [(item, "journal") for item in raw.get("journals", [])]
        else:
            logger.error(f"无法识别的 CCF JSON 格式: {type(raw)}")
            return []

        for entry in items:
            if isinstance(entry, tuple):
                item, vtype = entry
            else:
                item, vtype = entry, "conference"

            # 字段映射: abbr→abbreviation, fullName→full_name, rank→ccf_level
            abbrev = (item.get("abbr") or item.get("abbreviation") or
                      item.get("acronym") or "").strip()
            if not abbrev or abbrev in seen:
                continue
            seen.add(abbrev)

            full_name = (item.get("fullName") or item.get("full_name") or
                         item.get("name") or "").strip()

            # CCF 级别: "A" → "CCF-A"
            rank = (item.get("rank") or item.get("ccf_level") or
                    item.get("level") or "").strip().upper()
            if rank in ("A", "CCF-A"):
                ccf_level = "CCF-A"
            elif rank in ("B", "CCF-B"):
                ccf_level = "CCF-B"
            elif rank in ("C", "CCF-C"):
                ccf_level = "CCF-C"
            else:
                ccf_level = rank if rank.startswith("CCF-") else ""

            # 类型推断
            if not vtype or vtype == "conference":
                name_lower = (full_name + abbrev).lower()
                if any(kw in name_lower for kw in
                       ["transactions", "journal", "review", "letters", "magazine"]):
                    vtype = "journal"
                else:
                    vtype = "conference"

            # 研究领域: 从 category 字段映射
            category = item.get("category", "")
            areas = CCF_AREA_GUESS.get(abbrev, [])
            if not areas and category:
                for cat_key, cat_areas in self.CATEGORY_AREA_MAP.items():
                    if cat_key in category:
                        areas = cat_areas
                        break
            if not areas:
                areas = [category] if category else []

            caai_map = {"CCF-A": "CAAI-A", "CCF-B": "CAAI-B", "CCF-C": "CAAI-C"}
            caai = caai_map.get(ccf_level, "")

            venue = {
                "abbreviation": abbrev,
                "full_name": full_name,
                "ccf_level": ccf_level,
                "caai_level": caai,
                "type": vtype,
                "research_areas": areas,
                "acceptance_rate": 0.0, "avg_review_weeks": 0,
                "review_model": "", "publication_fee": 0, "is_oa": False,
                "next_deadline": "", "notification_date": "", "aims_scope": "",
            }
            venues.append(venue)

        return venues

    # ═══════════════════════════════════════════════════════
    # Tier 2: WikiCFP 截稿日期
    # ═══════════════════════════════════════════════════════

    def fetch_deadlines(self, venues: list[dict]) -> None:
        """从 WikiCFP 抓取每个会议的下轮截稿日期"""
        if self.no_deadlines:
            logger.info("[Tier 2] 跳过 WikiCFP（--no-deadlines）")
            return

        logger.info(f"[Tier 2] WikiCFP 截稿日期抓取（{len(venues)} 个）...")
        fetched = 0

        for i, v in enumerate(venues):
            if v["type"] == "journal":
                v["next_deadline"] = "N/A (rolling)"
                v["notification_date"] = "N/A"
                continue

            abbrev = v["abbreviation"]
            try:
                dl = self._wikicfp_lookup(abbrev)
                if dl:
                    if dl.get("deadline"):
                        v["next_deadline"] = dl["deadline"]
                    if dl.get("notification"):
                        v["notification_date"] = dl["notification"]
                    fetched += 1
                elif not v.get("next_deadline"):
                    v["next_deadline"] = ""
                    v["notification_date"] = ""
            except Exception as e:
                logger.debug(f"  {abbrev}: {e}")

            if i % 4 == 3:
                time.sleep(1.5)

        self.stats["deadlines_fetched"] = fetched
        logger.info(f"  WikiCFP 完成: {fetched}/{len(venues)}")

    def _wikicfp_lookup(self, abbrev: str) -> Optional[dict]:
        """搜索 WikiCFP 并返回 {deadline, notification}"""
        try:
            resp = self.session.get(
                WIKICFP_SEARCH, params={"q": f"{abbrev} 2027", "t": "n"}, timeout=12)
            resp.raise_for_status()

            # 从 HTML 中提取第一个匹配的 event 链接
            ids = re.findall(r"eventid=(\d+)", resp.text)
            if not ids:
                # 也尝试没有年份的搜索
                resp2 = self.session.get(
                    WIKICFP_SEARCH, params={"q": abbrev, "t": "n"}, timeout=12)
                ids = re.findall(r"eventid=(\d+)", resp2.text)

            if not ids:
                return None
            return self._wikicfp_detail(ids[0])
        except Exception:
            return None

    def _wikicfp_detail(self, event_id: str) -> Optional[dict]:
        """抓取 event 详情页提取截稿 + 通知日期"""
        try:
            resp = self.session.get(WIKICFP_EVENT.format(event_id), timeout=12)
            resp.raise_for_status()
            result = {}

            # 按模式提取日期
            date_pattern = r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})"
            months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                      "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

            # 找截稿日(通常在 Submission 附近)
            dl_section = re.split(r"(?i)(notification|acceptance)", resp.text)[0]
            dl_matches = re.findall(date_pattern, dl_section, re.I)
            if dl_matches:
                d, m, y = dl_matches[0]
                result["deadline"] = f"{y}-{months[m.lower()[:3]]:02d}-{int(d):02d}"

            # 找通知日
            notif_matches = re.findall(date_pattern, resp.text, re.I)
            if len(notif_matches) > len(dl_matches):
                d, m, y = notif_matches[-1]
                result["notification"] = f"{y}-{months[m.lower()[:3]]:02d}-{int(d):02d}"

            return result if result else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════
    # Tier 3: 统计补全
    # ═══════════════════════════════════════════════════════

    def enrich_with_stats(self, venues: list[dict]) -> None:
        """补全接收率、审稿周期、审稿模式"""
        logger.info("[Tier 3] 统计数据补全...")
        updated = 0
        for v in venues:
            abbrev = v["abbreviation"]
            stats = KNOWN_STATS.get(abbrev)
            if stats:
                for dst, src in [("acceptance_rate", "accept"),
                                 ("avg_review_weeks", "weeks"),
                                 ("review_model", "review")]:
                    cur = v.get(dst)
                    if cur in (None, 0, 0.0, ""):
                        v[dst] = stats[src]
                        updated += 1

            # 类型默认值
            defaults = J_DEFAULTS if v["type"] == "journal" else C_DEFAULTS
            for k, dv in defaults.items():
                if v.get(k) in (None, 0, 0.0, ""):
                    v[k] = dv
                    updated += 1

        logger.info(f"  统计补全: {updated} 字段")

    # ═══════════════════════════════════════════════════════
    # 合并 & 输出
    # ═══════════════════════════════════════════════════════

    def merge(self, new_list: list[dict], existing: list[dict]) -> list[dict]:
        """合并: 已有venue仅填补空字段(非--force模式)"""
        exist_map = {v["abbreviation"]: v for v in existing}
        merged = []

        for new_v in new_list:
            abbrev = new_v["abbreviation"]
            if abbrev in exist_map:
                old = exist_map.pop(abbrev)
                if self.force:
                    old.update(new_v)
                    self.stats["updated_fields"] += 1
                else:
                    changes = 0
                    for k, val in new_v.items():
                        if old.get(k) in (None, 0, 0.0, "", [], False):
                            old[k] = val
                            changes += 1
                    if changes:
                        self.stats["updated_fields"] += changes
                    else:
                        self.stats["unchanged"] += 1
                merged.append(old)
            else:
                merged.append(new_v)
                self.stats["new_venues"] += 1

        # 保留在已有但不在新数据中的(手动维护的)
        for old in exist_map.values():
            merged.append(old)
            self.stats["unchanged"] += 1

        return merged

    def write_output(self, venues: list[dict]) -> None:
        """写入 fetched_venues.py"""
        order = {"CCF-A": 0, "CCF-B": 1, "CCF-C": 2}
        venues.sort(key=lambda v: (order.get(v.get("ccf_level", ""), 99),
                                    v.get("type", ""), v.get("abbreviation", "")))

        clean = []
        for v in venues:
            c = {k: v[k] for k in v if not k.startswith("_")}
            c["acceptance_rate"] = float(c.get("acceptance_rate") or 0)
            c["avg_review_weeks"] = int(c.get("avg_review_weeks") or 0)
            c["publication_fee"] = int(c.get("publication_fee") or 0)
            c["is_oa"] = bool(c.get("is_oa"))
            clean.append(c)

        lines = [
            "# 自动生成的会议/期刊数据",
            f"# 生成时间: {datetime.now().isoformat()}",
            f"# 数据源: CCF 第七版 + WikiCFP + 社区统计",
            f"# venue 数量: {len(clean)}",
            "",
            "FETCHED_VENUES = [",
        ]

        keys = ["abbreviation", "full_name", "ccf_level", "caai_level", "type",
                "research_areas", "acceptance_rate", "avg_review_weeks",
                "review_model", "publication_fee", "is_oa",
                "next_deadline", "notification_date", "aims_scope"]

        for i, v in enumerate(clean):
            lines.append("  {")
            for j, key in enumerate(keys):
                val = v.get(key, "")
                if isinstance(val, str):
                    s = json.dumps(val, ensure_ascii=False)
                elif isinstance(val, list):
                    s = json.dumps(val, ensure_ascii=False)
                elif isinstance(val, bool):
                    s = "True" if val else "False"
                elif isinstance(val, float):
                    s = f"{val:.4f}" if val != int(val) else f"{val:.1f}"
                elif isinstance(val, int):
                    s = str(val)
                else:
                    s = json.dumps(val, ensure_ascii=False)
                lines.append(f'    "{key}": {s}{"," if j < len(keys) - 1 else ""}')
            lines.append(f"  }}{',' if i < len(clean) - 1 else ''}")

        lines.extend(["", "]", ""])
        OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"已写入: {OUTPUT_FILE} ({len(clean)} venues)")

    # ═══════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════

    def run(self, dry_run: bool = False) -> dict:
        existing = []
        try:
            from knowledge.fetched_venues import FETCHED_VENUES
            existing = list(FETCHED_VENUES)
            logger.info(f"已加载 {len(existing)} 个现有 venue")
        except ImportError:
            logger.info("无现有 fetched_venues.py, 全新生成")

        # Tier 1
        new_venues = self.fetch_ccf_list()
        if not new_venues:
            logger.warning("CCF 列表为空, 使用已有数据")
            new_venues = copy.deepcopy(existing)

        # Tier 2
        self.fetch_deadlines(new_venues)

        # Tier 3
        self.enrich_with_stats(new_venues)

        # 合并
        merged = self.merge(new_venues, existing)
        logger.info(f"合并: {len(merged)} total, +{self.stats['new_venues']} new, "
                     f"~{self.stats['updated_fields']} fields updated, "
                     f"={self.stats['unchanged']} unchanged")

        if dry_run:
            logger.info("[DRY RUN] 不写入")
            exist_abbrevs = {v["abbreviation"] for v in existing}
            new_only = [v for v in merged if v["abbreviation"] not in exist_abbrevs]
            if new_only:
                print(f"\n将新增 {len(new_only)} 个 venue:")
                for v in new_only:
                    print(f"  + {v['abbreviation']} ({v.get('ccf_level','?')}) — {v.get('full_name','')}")
            else:
                print("\n无新增(所有CCF venue已存在)")
        else:
            self.write_output(merged)

        return self.stats


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="投稿推荐 Agent — 知识库数据抓取工具")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有字段")
    parser.add_argument("--dry-run", action="store_true", help="仅预览变更, 不写入")
    parser.add_argument("--no-deadlines", action="store_true", help="跳过 WikiCFP 截稿日期抓取")
    parser.add_argument("--local", type=str, metavar="FILE",
                        help="使用本地 CCF JSON 文件(跳过在线获取)")
    args = parser.parse_args()

    fetcher = VenueFetcher(force=args.force, no_deadlines=args.no_deadlines,
                           local_ccf=args.local)
    s = fetcher.run(dry_run=args.dry_run)

    print(f"\n--- 统计 ---")
    print(f"  CCF 拉取: {s['ccf_fetched']}")
    print(f"  截稿日期: {s['deadlines_fetched']}")
    print(f"  新增 venue: {s['new_venues']}")
    print(f"  更新字段: {s['updated_fields']}")
    print(f"  未变更: {s['unchanged']}")


if __name__ == "__main__":
    main()
