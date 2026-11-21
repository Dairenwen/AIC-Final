"""意见关系检测：SHARED / RELATED / CONFLICT 的可解释规则。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID, uuid5

from langgraph_agent.agent.state import WorkspaceTaskState
from langgraph_agent.schemas import GraphRunStatus

# 词项重合 / 字符相似度阈值（可单测、可解释）。
SHARED_SIMILARITY = 0.78
SHARED_OVERLAP = 0.60
SHARED_CONTAINMENT = 0.85
RELATED_SIMILARITY = 0.50
RELATED_OVERLAP = 0.30
RELATED_WEAK_OVERLAP = 0.18
RELATED_CONTAINMENT = 0.65
CONFLICT_TOPIC_OVERLAP = 0.20

_RELATION_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "is",
    "it",
    "must",
    "need",
    "needs",
    "of",
    "on",
    "or",
    "please",
    "should",
    "the",
    "to",
    "with",
    "请",
    "建议",
    "需要",
    "应该",
    "必须",
}

# 词面归一：同义/词形 → 同一词根，降低漏合。
_TERM_ALIASES = {
    "clarification": "clarify",
    "clarifications": "clarify",
    "clearer": "clarify",
    "clarified": "clarify",
    "clarifying": "clarify",
    "explain": "clarify",
    "explanation": "clarify",
    "explanations": "clarify",
    "sampling": "sample",
    "samples": "sample",
    "sampled": "sample",
    "adding": "add",
    "added": "add",
    "include": "add",
    "includes": "add",
    "including": "add",
    "included": "add",
    "provide": "add",
    "provides": "add",
    "supplement": "add",
    "supplementary": "add",
    "remove": "remove",
    "removes": "remove",
    "removing": "remove",
    "removed": "remove",
    "delete": "remove",
    "deletes": "remove",
    "deleting": "remove",
    "deleted": "remove",
    "exclude": "remove",
    "excludes": "remove",
    "excluding": "remove",
    "excluded": "remove",
    "increase": "increase",
    "increases": "increase",
    "increasing": "increase",
    "increased": "increase",
    "decrease": "decrease",
    "decreases": "decrease",
    "decreasing": "decrease",
    "decreased": "decrease",
    "reduce": "decrease",
    "reduces": "decrease",
    "reducing": "decrease",
    "reduced": "decrease",
    "experiments": "experiment",
    "experimental": "experiment",
    "studies": "study",
    "baselines": "baseline",
    "datasets": "dataset",
    "figures": "figure",
    "tables": "table",
    "methods": "method",
    "results": "result",
    "limitations": "limitation",
    "ablations": "ablation",
    "补充": "增加",
    "添加": "增加",
    "加入": "增加",
    "增设": "增加",
    "新增": "增加",
    "删除": "删除",
    "去除": "删除",
    "去掉": "删除",
    "移除": "删除",
    "减少": "减少",
    "降低": "减少",
    "澄清": "澄清",
    "说明": "澄清",
    "解释": "澄清",
    "阐明": "澄清",
    "消融实验": "消融",
    "消融研究": "消融",
    "消融分析": "消融",
}

# 短语级同义（先于分词替换，覆盖跨词同义）。
_PHRASE_ALIASES: tuple[tuple[str, str], ...] = (
    ("ablation study", "ablation"),
    ("ablation studies", "ablation"),
    ("ablation experiment", "ablation"),
    ("ablation experiments", "ablation"),
    ("sample size", "sample"),
    ("sampling procedure", "sample procedure"),
    ("data set", "dataset"),
    ("data sets", "dataset"),
    ("train/val/test", "dataset split"),
    ("training validation test", "dataset split"),
    ("消融实验", "消融"),
    ("消融研究", "消融"),
    ("消融分析", "消融"),
    ("样本量", "样本"),
    ("样本规模", "样本"),
    ("数据集划分", "数据划分"),
    ("数据划分", "数据划分"),
)

_OPPOSING_MARKER_PAIRS = (
    ("increase", "decrease"),
    ("add", "remove"),
    ("accept", "reject"),
    ("include", "exclude"),
    ("增加", "减少"),
    ("增加", "删除"),
    ("加入", "删除"),
    ("接受", "拒绝"),
    ("提高", "降低"),
)

_ACTION_TERMS = {
    "increase",
    "decrease",
    "add",
    "remove",
    "accept",
    "reject",
    "include",
    "exclude",
    "增加",
    "减少",
    "删除",
    "加入",
    "接受",
    "拒绝",
    "提高",
    "降低",
    "澄清",
    "clarify",
}

# 中文词典：归一化后优先整词切分，避免无意义双字稀释重合度。
_CHINESE_LEXICON = tuple(
    sorted(
        {
            *{source for source, _ in _PHRASE_ALIASES if re.fullmatch(r"[一-鿿]+", source)},
            *{target for _, target in _PHRASE_ALIASES if re.fullmatch(r"[一-鿿]+", target)},
            *{source for source in _TERM_ALIASES if re.fullmatch(r"[一-鿿]+", source)},
            *{target for target in _TERM_ALIASES.values() if re.fullmatch(r"[一-鿿]+", target)},
            "增加",
            "减少",
            "删除",
            "澄清",
            "消融",
            "样本",
            "模块",
            "贡献",
            "验证",
            "数据划分",
            "方法",
            "局限",
            "实验",
            "基线",
            "结果",
            "图",
            "表",
        },
        key=len,
        reverse=True,
    )
)

_PUNCT_RE = re.compile(r"[，。！？、；：,.!?;:\"'“”‘’（）()【】\[\]《》<>/\\|]+")
_SPACE_RE = re.compile(r"[\s　]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")
_CHINESE_STOP_CHARS = set("的了和与及在对把被将从比也是有以而及其等")


def normalize_relation_text(text: str) -> str:
    """归一化比较文本：小写、去标点、短语/中文同义替换。"""
    normalized = str(text or "").casefold().strip()
    normalized = _PUNCT_RE.sub(" ", normalized)
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    for source, target in sorted(_PHRASE_ALIASES, key=lambda item: len(item[0]), reverse=True):
        if source in normalized:
            normalized = normalized.replace(source, target)
    # 中文词面别名按长度优先做子串替换，避免整句无法命中。
    chinese_aliases = sorted(
        (
            (source, target)
            for source, target in _TERM_ALIASES.items()
            if re.fullmatch(r"[一-鿿]+", source) and len(source) >= 2
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for source, target in chinese_aliases:
        if source in normalized:
            normalized = normalized.replace(source, target)
    return normalized


def _tokenize_chinese_run(run: str) -> list[str]:
    """词典优先的中文切分；未命中片段再按双字回退。"""
    pieces: list[str] = []
    index = 0
    length = len(run)
    while index < length:
        matched = None
        for word in _CHINESE_LEXICON:
            if run.startswith(word, index):
                matched = word
                break
        if matched:
            pieces.append(_TERM_ALIASES.get(matched, matched))
            index += len(matched)
            continue
        char = run[index]
        if char in _RELATION_STOP_WORDS or char in _CHINESE_STOP_CHARS:
            index += 1
            continue
        if index + 1 < length:
            # 若下一字可开启词典词，则当前单字单独保留（如有信息量）。
            starts_lexicon = any(run.startswith(word, index + 1) for word in _CHINESE_LEXICON)
            if starts_lexicon:
                if char not in _CHINESE_STOP_CHARS:
                    pieces.append(char)
                index += 1
                continue
            bigram = run[index : index + 2]
            pieces.append(_TERM_ALIASES.get(bigram, bigram))
            index += 2
            continue
        pieces.append(char)
        index += 1
    return pieces


def relation_terms(text: str) -> set[str]:
    """抽取可比较词项；中文优先词典切分，降低噪声双字。"""
    normalized = normalize_relation_text(text)
    terms: set[str] = set()
    for token in _TOKEN_RE.findall(normalized):
        if re.fullmatch(r"[a-z0-9]+", token):
            if token in _RELATION_STOP_WORDS or token.isdigit():
                continue
            terms.add(_TERM_ALIASES.get(token, token))
            continue
        for piece in _tokenize_chinese_run(token):
            if piece and piece not in _RELATION_STOP_WORDS:
                terms.add(piece)
    return terms


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _containment(left: set[str], right: set[str]) -> float:
    """较短集合被较长集合覆盖的比例。"""
    if not left or not right:
        return 0.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter & longer) / len(shorter)


def _topic_terms(terms: set[str]) -> set[str]:
    return {term for term in terms if term not in _ACTION_TERMS}


def _has_opposing_markers(left_lower: str, right_lower: str) -> bool:
    for positive, negative in _OPPOSING_MARKER_PAIRS:
        left_has_pos = positive in left_lower
        left_has_neg = negative in left_lower
        right_has_pos = positive in right_lower
        right_has_neg = negative in right_lower
        if (left_has_pos and right_has_neg) or (left_has_neg and right_has_pos):
            return True
    return False


def _score_pair(left: str, right: str) -> dict[str, float | set[str] | str]:
    left_norm = normalize_relation_text(left)
    right_norm = normalize_relation_text(right)
    left_terms = relation_terms(left)
    right_terms = relation_terms(right)
    similarity = SequenceMatcher(None, left_norm, right_norm).ratio()
    overlap = _jaccard(left_terms, right_terms)
    containment = _containment(left_terms, right_terms)
    topic_overlap = _jaccard(_topic_terms(left_terms), _topic_terms(right_terms))
    return {
        "left_norm": left_norm,
        "right_norm": right_norm,
        "left_terms": left_terms,
        "right_terms": right_terms,
        "similarity": similarity,
        "overlap": overlap,
        "containment": containment,
        "topic_overlap": topic_overlap,
    }


def detect_relation_type(left: str, right: str) -> str | None:
    """比较两条建议文本，返回 SHARED / RELATED / CONFLICT 或 None。

    SAME 语义统一映射为 SHARED。阈值见模块常量。
    """
    scores = _score_pair(left, right)
    left_norm = str(scores["left_norm"])
    right_norm = str(scores["right_norm"])
    similarity = float(scores["similarity"])
    overlap = float(scores["overlap"])
    containment = float(scores["containment"])
    topic_overlap = float(scores["topic_overlap"])

    if _has_opposing_markers(left_norm, right_norm) and (
        overlap >= CONFLICT_TOPIC_OVERLAP
        or topic_overlap >= CONFLICT_TOPIC_OVERLAP
        or (
            bool(_topic_terms(scores["left_terms"]) & _topic_terms(scores["right_terms"]))
            and max(overlap, topic_overlap) >= 0.15
        )
    ):
        return "CONFLICT"

    if (
        similarity >= SHARED_SIMILARITY
        or overlap >= SHARED_OVERLAP
        or (
            containment >= SHARED_CONTAINMENT
            and overlap >= RELATED_OVERLAP
            and min(len(left_norm), len(right_norm)) >= 6
        )
        or (
            # 同动作 + 主题高度重合：覆盖中文「补充/增加消融」类同义改写。
            containment >= 0.75
            and topic_overlap >= 0.45
            and not _has_opposing_markers(left_norm, right_norm)
        )
    ):
        return "SHARED"

    # RELATED 要求一定主题重合，避免纯字符相似的无关句误判。
    if overlap >= RELATED_OVERLAP or topic_overlap >= RELATED_OVERLAP:
        return "RELATED"
    if similarity >= RELATED_SIMILARITY and (
        overlap >= RELATED_WEAK_OVERLAP or topic_overlap >= RELATED_WEAK_OVERLAP
    ):
        return "RELATED"
    if (
        containment >= RELATED_CONTAINMENT
        and (overlap >= RELATED_WEAK_OVERLAP or topic_overlap >= RELATED_WEAK_OVERLAP)
        and similarity >= 0.30
    ):
        return "RELATED"
    return None


# 兼容旧名：测试与迁移期 re-export。
_relation_type = detect_relation_type
_relation_terms = relation_terms


def explain_relation(left: str, right: str, relation_type: str) -> str:
    """生成中文可解释说明，包含关键分数与命中规则。"""
    scores = _score_pair(left, right)
    similarity = float(scores["similarity"])
    overlap = float(scores["overlap"])
    containment = float(scores["containment"])
    left_norm = str(scores["left_norm"])
    right_norm = str(scores["right_norm"])
    shared_terms = sorted(scores["left_terms"] & scores["right_terms"])  # type: ignore[operator]
    shared_preview = "、".join(shared_terms[:6]) if shared_terms else "无"

    if relation_type == "CONFLICT":
        return (
            f"判定 CONFLICT：检测到相反动作词且主题重合 {overlap:.2f}"
            f"（阈值 ≥{CONFLICT_TOPIC_OVERLAP:.2f}）；"
            f"共享词项：{shared_preview}。"
        )
    if relation_type == "SHARED":
        reasons: list[str] = []
        if similarity >= SHARED_SIMILARITY:
            reasons.append(f"文本相似度 {similarity:.2f}≥{SHARED_SIMILARITY:.2f}")
        if overlap >= SHARED_OVERLAP:
            reasons.append(f"词项重合 {overlap:.2f}≥{SHARED_OVERLAP:.2f}")
        if containment >= SHARED_CONTAINMENT and overlap >= RELATED_OVERLAP:
            reasons.append(
                f"较短方覆盖率 {containment:.2f}≥{SHARED_CONTAINMENT:.2f}"
            )
        reason_text = "；".join(reasons) if reasons else "综合文本特征接近"
        return (
            f"判定 SHARED：{reason_text}；"
            f"共享词项：{shared_preview}。"
            "语义为同一修改点，可合并为共享建议并分别回复。"
        )
    if relation_type == "RELATED":
        return (
            f"判定 RELATED：文本相似度 {similarity:.2f} 或词项重合 {overlap:.2f}"
            f"达到相关阈值（sim≥{RELATED_SIMILARITY:.2f} 或 overlap≥{RELATED_OVERLAP:.2f}），"
            f"但未达 SHARED；共享词项：{shared_preview}。不合并，分别保留。"
        )
    return (
        f"未建立关系：相似度 {similarity:.2f}，词项重合 {overlap:.2f}，"
        f"覆盖率 {containment:.2f}；左「{left_norm[:24]}」右「{right_norm[:24]}」。"
    )


def build_relation_proposals(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对已确认建议两两比较，生成关系提案列表。"""
    relations: list[dict[str, Any]] = []
    for left_index, left in enumerate(suggestions):
        for right in suggestions[left_index + 1 :]:
            left_text = str(left.get("canonical_text") or "")
            right_text = str(right.get("canonical_text") or "")
            relation_type = detect_relation_type(left_text, right_text)
            if relation_type is None:
                continue
            # SAME 统一成 SHARED，与确认/落库分支一致。
            if relation_type == "SAME":
                relation_type = "SHARED"
            left_id = str(left["proposal_id"])
            right_id = str(right["proposal_id"])
            relations.append(
                {
                    "relation_id": f"R-{left_id}-{right_id}",
                    "type": relation_type,
                    "suggestion_ids": [left_id, right_id],
                    "explanation": explain_relation(
                        left_text, right_text, relation_type
                    ),
                }
            )
    return relations


def apply_relation_confirmation(
    suggestions: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """应用用户确认的关系：SHARED/SAME 写 merge_group_key，CONFLICT 写 conflict_group_key。"""
    if payload.get("approved") is False:
        raise ValueError("意见关系未确认，图不能继续")
    relation_by_id = {item["relation_id"]: item for item in relations}
    approved = payload.get("approved_relation_ids")
    if approved is None:
        approved_ids = list(relation_by_id)
    elif isinstance(approved, list):
        approved_ids = [str(item) for item in approved]
    else:
        raise ValueError("approved_relation_ids 必须是数组")
    for relation_id in approved_ids:
        if relation_id not in relation_by_id:
            raise ValueError(f"未知关系提案：{relation_id}")

    persistable = [dict(item) for item in suggestions]
    suggestion_by_id = {item["proposal_id"]: item for item in persistable}
    confirmed_relations = [relation_by_id[item] for item in approved_ids]
    for relation in confirmed_relations:
        group_key = f"{relation['type'].casefold()}:{relation['relation_id']}"
        for proposal_id in relation["suggestion_ids"]:
            suggestion = suggestion_by_id.get(proposal_id)
            if suggestion is None:
                raise ValueError(f"关系引用未知建议：{proposal_id}")
            if relation["type"] in {"SHARED", "SAME"}:
                suggestion["merge_group_key"] = group_key
            elif relation["type"] == "CONFLICT":
                suggestion["conflict_group_key"] = group_key
    return persistable, confirmed_relations


def _interaction_id(state: WorkspaceTaskState, interaction_type: str) -> UUID:
    return uuid5(UUID(str(state["run_id"])), interaction_type)


def detect_relations(state: WorkspaceTaskState) -> dict[str, object]:
    """图节点：生成关系提案并进入 CONFIRM_RELATIONS。"""
    draft_refs = dict(state.get("draft_refs", {}))
    suggestions = draft_refs.get("confirmed_suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("缺少已确认的建议清单")

    relations = build_relation_proposals(suggestions)
    interaction_id = _interaction_id(state, "CONFIRM_RELATIONS")
    draft_refs["relation_proposals"] = relations
    return {
        "phase": "CONFIRM_RELATIONS",
        "pending_interaction_id": interaction_id,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.WAITING_USER,
    }


# 旧私有名 re-export，供 graph / 既有测试兼容。
_apply_relation_confirmation = apply_relation_confirmation
