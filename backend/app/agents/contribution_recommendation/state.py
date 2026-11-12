"""
智研 · 投稿推荐 Agent — LangGraph 统一状态对象
"""

from typing import TypedDict, Optional


class PaperFeatures(TypedDict, total=False):
    sub_fields: list[str]
    methodology_paradigm: str
    experiment_completeness: float
    novelty_level: str          # incremental / substantial / breakthrough
    datasets_used: list[str]
    reference_venue_distribution: dict
    key_techniques: list[str]


class VenueInfo(TypedDict, total=False):
    venue_id: str
    type: str                   # conference / journal
    abbreviation: str
    full_name: str
    ccf_level: str
    caai_level: str
    research_areas: list[str]
    acceptance_rate: float
    avg_review_weeks: int
    review_model: str
    publication_fee: float
    is_oa: bool
    next_deadline: str
    notification_date: str


class MatchScore(TypedDict, total=False):
    overall: float
    topic_similarity: float
    methodology_alignment: float
    experiment_completeness_fit: float
    novelty_level_fit: float
    citation_coupling: float


class Recommendation(TypedDict, total=False):
    venue: VenueInfo
    tier: str                   # sprint / match / safety
    match_score: MatchScore
    match_details: dict
    estimated_acceptance_prob: str
    confidence: float
    risks: list[str]
    strengths: list[str]
    differentiation: str


class SubmissionRecommendState(TypedDict, total=False):
    task_id: str
    paper_id: str
    user_id: str
    user_preferences: dict
    parsed_paper: dict
    quality_estimate: dict
    compliance_result: dict
    paper_features: PaperFeatures
    candidate_venues: list[VenueInfo]
    match_scores: dict[str, MatchScore]
    venue_dynamic_info: dict
    competitive_analysis: dict
    recommendations: list[Recommendation]
    submission_checklist: dict
    submission_strategy: dict
    final_report: str
    interactive_data: dict
    comparison_matrix: dict
    thinking_trace: list[dict]
    user_satisfied: bool
    iteration_count: int
    errors: list[str]
