from __future__ import annotations

import json
from pathlib import Path

import click
from sqlalchemy import select, text

from ..extensions import db
from ..models import (
    AcademicFigureRun,
    Agent,
    Artifact,
    ArxivDailyRun,
    Conversation,
    DocumentVersion,
    Message,
    PaperReadingRun,
    PatentDraftingRun,
    PersonalKnowledgeFolder,
    PersonalKnowledgePaper,
    Project,
    ProjectDocument,
    ProjectMember,
    Tool,
)
from .skill_importer import sync_skills


PAPER_READING_AGENT = {
    "code": "paper_reading",
    "name": "论文精读",
    "category": "论文研读",
    "description": "面向单篇 PDF 或 arXiv 论文，生成带页码、章节与证据状态的结构化精读报告。",
    "version": 1,
    "config_json": {
        "route": "/agents/paper-reading",
        "runtime": "zhiyan-paper-reading-agent-v0.6.4",
        "capabilities": [
            "pdf_parsing",
            "claim_evidence",
            "method_analysis",
            "experiment_analysis",
            "scientific_object_analysis",
            "table_evidence_audit",
            "paper_scoped_qa",
            "reliability_check",
            "performance_diagnostics",
        ],
    },
    "status": "ACTIVE",
}

INNOVATION_POINT_AGENT = {
    "code": "innovation_point_generation",
    "name": "创新点生成",
    "category": "创新挖掘",
    "description": "基于本地文献语料完成趋势分析、研究空白识别、创新方案生成、四维评分与证据绑定。",
    "version": 1,
    "config_json": {
        "route": "/agents/innovation-point-generation",
        "runtime": "paper-insight-generate",
        "capabilities": [
            "trend_analysis",
            "gap_identification",
            "idea_generation",
            "innovation_evaluation",
            "evidence_binding",
        ],
    },
    "status": "ACTIVE",
}

ACADEMIC_COMPLIANCE_AGENT = {
    "code": "academic_compliance",
    "name": "学术合规性检测",
    "category": "论文质控",
    "description": "检查论文规范、引用与参考文献、图表一致性和投稿格式，输出风险等级、合规得分与修改建议。",
    "version": 1,
    "config_json": {
        "route": "/agents/academic-compliance",
        "runtime": "academic_compliance_agent",
        "capabilities": [
            "manuscript_parsing",
            "paper_norm_check",
            "citation_verification",
            "figure_table_consistency",
            "submission_format_check",
            "compliance_report",
        ],
    },
    "status": "ACTIVE",
}

REVIEWER_COMMENTS_AGENT = {
    "code": "reviewer_comments",
    "name": "审稿意见解析与引导回复",
    "category": "论文返修",
    "description": "拆解审稿意见，识别问题类型、修改证据和优先级，生成逐条回复策略、回复信草稿和返修清单。",
    "version": 1,
    "config_json": {
        "route": "/agents/reviewer-comments",
        "runtime": "reviewer_comments",
        "capabilities": [
            "comment_splitting",
            "severity_analysis",
            "reply_strategy",
            "response_letter",
            "revision_checklist",
        ],
    },
    "status": "ACTIVE",
}

CONTRIBUTION_RECOMMENDATION_AGENT = {
    "code": "contribution_recommendation",
    "name": "投稿推荐",
    "category": "投稿决策",
    "description": "根据论文主题、方法、实验完整度和投稿偏好，推荐冲刺、匹配、保底三档会议/期刊，并输出准备清单。",
    "version": 1,
    "config_json": {
        "route": "/agents/contribution-recommendation",
        "runtime": "contribution_recommendation",
        "capabilities": [
            "paper_feature_extraction",
            "venue_matching",
            "acceptance_estimation",
            "submission_strategy",
            "checklist_generation",
        ],
    },
    "status": "ACTIVE",
}

ACADEMIC_TRANSLATION_AGENT = {
    "code": "academic_translation",
    "name": "学术翻译",
    "category": "论文处理",
    "description": "使用固定本地学术翻译模型完成论文术语约束翻译，保护公式、引用与数值，并支持版式保留和双语对照产物。",
    "version": 1,
    "config_json": {
        "route": "/agents/academic-translation",
        "runtime": "academic-translation-agent",
        "capabilities": [
            "academic_translation",
            "terminology_consistency",
            "formula_and_citation_protection",
            "layout_preserving_pdf",
            "bilingual_export",
            "quality_report",
        ],
    },
    "status": "ACTIVE",
}

PATENT_DRAFTING_AGENT = {
    "code": "patent_drafting",
    "name": "专利撰写",
    "category": "知识产权",
    "description": "基于技术材料生成候选专利点，经人工选择后完成 CNIPA 检索、差异分析、技术交底书、权利要求草案与证据复核包。",
    "version": 1,
    "config_json": {
        "route": "/agents/patent-drafting",
        "runtime": "zhiyan-patent-drafting-agent-v1.0.2",
        "capabilities": [
            "patent_point_generation",
            "human_in_the_loop_selection",
            "cnipa_prior_art_search",
            "technical_disclosure_drafting",
            "claims_drafting",
            "claim_validation",
            "evidence_review",
            "docx_export",
        ],
    },
    "status": "ACTIVE",
}

ACADEMIC_FIGURE_AGENT = {
    "code": "academic_figure",
    "name": "绘图创作",
    "category": "科研可视化",
    "description": "根据实验数据、论文上下文或草图规划并生成中英文学术图表，同时交付可复现代码、规范化数据、图注和质量报告。",
    "version": 1,
    "config_json": {
        "route": "/agents/academic-figure",
        "runtime": "academic-figure-agent",
        "capabilities": [
            "statistical_figure",
            "flowchart",
            "image_panel",
            "bilingual_rendering",
            "reproducible_code",
            "figure_quality_check",
        ],
    },
    "status": "ACTIVE",
}

ARXIV_DAILY_AGENT = {
    "code": "arxiv_daily",
    "name": "学术速递",
    "category": "论文追踪",
    "description": "同步 arXivDaily 计算机科学分类论文，提供中文标题与摘要、作者机构、原版 PDF 阅读和按分类检索的每日论文流。",
    "version": 1,
    "config_json": {
        "route": "/agents/academic-daily",
        "runtime": "arxiv-daily-agent-v0.1.0",
        "source": "https://www.arxivdaily.com/",
        "capabilities": [
            "daily_paper_feed",
            "cs_category_browsing",
            "bilingual_abstract",
            "paper_search",
            "original_pdf_reader",
            "database_snapshot",
        ],
    },
    "status": "ACTIVE",
}

FORMULA_IMAGE_TO_LATEX_TOOL = {
    "code": "formula_image_to_latex",
    "name": "公式图片转 LaTeX",
    "category": "文档处理",
    "description": "识别公式截图或扫描图片，输出可编辑、复制和下载的 LaTeX 源码。",
    "version": 1,
    "config_json": {
        "route": "/tools/formula-to-latex",
        "runtime": "UniMERNet",
        "capabilities": ["formula_image_recognition", "latex_export"],
    },
    "risk_level": 0,
    "status": "ACTIVE",
}

BUILTIN_RESEARCH_TOOLS = (
    FORMULA_IMAGE_TO_LATEX_TOOL,
    {
        "code": "literature_ppt",
        "name": "文献 PPT 绘制",
        "category": "文献汇报",
        "description": "上传 PDF 文献，自动提取可追溯证据、原文表格和插图，生成可编辑科研汇报 PPT。",
        "version": 1,
        "config_json": {
            "route": "/tools/literature-ppt",
            "runtime": "literature_ppt_tools",
            "capabilities": ["pdf_parsing", "evidence_trace", "editable_pptx", "table_and_figure_slides"],
        },
        "risk_level": 0,
        "status": "ACTIVE",
    },
    {
        "code": "citation_formatter",
        "name": "文献引用格式化",
        "category": "论文写作",
        "description": "填写论文元数据，一次生成 BibTeX、APA、GB/T 7714 和行内引用。",
        "version": 1,
        "config_json": {
            "route": "/tools/citation-formatter",
            "runtime": "built-in",
            "capabilities": ["bibtex", "apa", "gbt7714", "inline_citation"],
        },
        "risk_level": 0,
        "status": "ACTIVE",
    },
    {
        "code": "table_converter",
        "name": "科研表格转换",
        "category": "数据处理",
        "description": "将 CSV 或 TSV 实验数据转换为规范的 Markdown 与 booktabs LaTeX 表格。",
        "version": 1,
        "config_json": {
            "route": "/tools/table-converter",
            "runtime": "built-in",
            "capabilities": ["csv_parser", "markdown_table", "latex_table"],
        },
        "risk_level": 0,
        "status": "ACTIVE",
    },
    {
        "code": "text_statistics",
        "name": "学术文本统计",
        "category": "文本分析",
        "description": "统计中英文词数、句段、平均句长、阅读时间和高频关键词。",
        "version": 1,
        "config_json": {
            "route": "/tools/text-statistics",
            "runtime": "built-in",
            "capabilities": ["word_count", "readability_metrics", "keyword_frequency"],
        },
        "risk_level": 0,
        "status": "ACTIVE",
    },
    {
        "code": "markdown_to_docx",
        "name": "Markdown 转 Word",
        "category": "文档处理",
        "description": "将含标题、列表、代码、引用和表格的 Markdown 科研文稿导出为 DOCX。",
        "version": 1,
        "config_json": {
            "route": "/tools/markdown-to-docx",
            "runtime": "patent-drafting/md_to_docx",
            "capabilities": ["markdown_parser", "docx_export", "table_export"],
        },
        "risk_level": 0,
        "status": "ACTIVE",
    },
)


def register_catalog_cli(app) -> None:
    @app.cli.command("init-research-workspace")
    def init_research_workspace() -> None:
        """Create project workspace tables and upgrade task ownership columns."""
        Project.__table__.create(bind=db.engine, checkfirst=True)
        ProjectMember.__table__.create(bind=db.engine, checkfirst=True)
        db.session.execute(text("ALTER TABLE zhiyan.tasks ADD COLUMN IF NOT EXISTS project_id UUID"))
        db.session.execute(text("ALTER TABLE zhiyan.conversations ADD COLUMN IF NOT EXISTS project_id UUID"))
        db.session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_project_id ON zhiyan.tasks (project_id)")
        )
        db.session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_conversations_project_id ON zhiyan.conversations (project_id)")
        )
        db.session.commit()
        for model in (
            Conversation,
            Message,
            ProjectDocument,
            DocumentVersion,
            Artifact,
        ):
            model.__table__.create(bind=db.engine, checkfirst=True)
        click.echo("已初始化科研项目工作区、对话、文档版本与研究产物表")

    @app.cli.command("init-academic-space")
    def init_academic_space() -> None:
        """Create user-isolated personal knowledge base tables."""
        PersonalKnowledgeFolder.__table__.create(bind=db.engine, checkfirst=True)
        PersonalKnowledgePaper.__table__.create(bind=db.engine, checkfirst=True)
        click.echo("已初始化学术空间个人知识库表")

    @app.cli.command("sync-skills")
    @click.option("--file", "source_file", type=click.Path(path_type=Path), default=None)
    def sync_skills_command(source_file: Path | None) -> None:
        """Download crawled research skills and publish them in the skill catalog."""
        path = source_file or app.config["SKILL_CRAWL_FILE"]
        if not path.exists():
            raise click.ClickException(f"技能文件不存在: {path}")
        try:
            result = sync_skills(
                path,
                timeout=app.config["SKILL_IMPORT_TIMEOUT_SECONDS"],
                max_file_bytes=app.config["SKILL_IMPORT_MAX_FILE_BYTES"],
                max_total_bytes=app.config["SKILL_IMPORT_MAX_TOTAL_BYTES"],
            )
        except Exception:
            db.session.rollback()
            raise
        click.echo(json.dumps(result, ensure_ascii=False))

    @app.cli.command("sync-builtin-tools")
    def sync_builtin_tools() -> None:
        """Register or refresh built-in research tool catalog entries."""
        for definition in BUILTIN_RESEARCH_TOOLS:
            tool = db.session.scalar(select(Tool).where(Tool.code == definition["code"]))
            if tool is None:
                tool = Tool(**definition)
                db.session.add(tool)
            else:
                for key, value in definition.items():
                    setattr(tool, key, value)
        db.session.commit()
        click.echo(f"已同步 {len(BUILTIN_RESEARCH_TOOLS)} 个内置科研工具")

    @app.cli.command("init-paper-reading")
    def init_paper_reading() -> None:
        """Create the paper reading run table and register Agent 0.6.4."""
        PaperReadingRun.__table__.create(bind=db.engine, checkfirst=True)
        agent = db.session.scalar(select(Agent).where(Agent.code == PAPER_READING_AGENT["code"]))
        if agent is None:
            agent = Agent(**PAPER_READING_AGENT)
            db.session.add(agent)
        else:
            for key, value in PAPER_READING_AGENT.items():
                setattr(agent, key, value)
        db.session.commit()
        click.echo("已初始化论文精读 Agent 0.6.4 与运行记录表")

    @app.cli.command("sync-builtin-agents")
    def sync_builtin_agents() -> None:
        """Register or refresh built-in Agent catalog entries."""
        for definition in (
            PAPER_READING_AGENT,
            INNOVATION_POINT_AGENT,
            ACADEMIC_COMPLIANCE_AGENT,
            REVIEWER_COMMENTS_AGENT,
            CONTRIBUTION_RECOMMENDATION_AGENT,
            ACADEMIC_TRANSLATION_AGENT,
            PATENT_DRAFTING_AGENT,
            ACADEMIC_FIGURE_AGENT,
            ARXIV_DAILY_AGENT,
        ):
            agent = db.session.scalar(select(Agent).where(Agent.code == definition["code"]))
            if agent is None:
                agent = Agent(**definition)
                db.session.add(agent)
            else:
                for key, value in definition.items():
                    setattr(agent, key, value)
        db.session.commit()
        click.echo("已同步内置 Agent")

    @app.cli.command("init-patent-drafting")
    def init_patent_drafting() -> None:
        """Create the patent run table and register the built-in Agent."""
        PatentDraftingRun.__table__.create(bind=db.engine, checkfirst=True)
        agent = db.session.scalar(select(Agent).where(Agent.code == PATENT_DRAFTING_AGENT["code"]))
        if agent is None:
            agent = Agent(**PATENT_DRAFTING_AGENT)
            db.session.add(agent)
        else:
            for key, value in PATENT_DRAFTING_AGENT.items():
                setattr(agent, key, value)
        db.session.commit()
        click.echo("已初始化专利撰写 Agent 与运行记录表")

    @app.cli.command("init-academic-figure")
    def init_academic_figure() -> None:
        """Create the academic figure run table and register the built-in Agent."""
        AcademicFigureRun.__table__.create(bind=db.engine, checkfirst=True)
        agent = db.session.scalar(select(Agent).where(Agent.code == ACADEMIC_FIGURE_AGENT["code"]))
        if agent is None:
            agent = Agent(**ACADEMIC_FIGURE_AGENT)
            db.session.add(agent)
        else:
            for key, value in ACADEMIC_FIGURE_AGENT.items():
                setattr(agent, key, value)
        db.session.commit()
        click.echo("已初始化绘图创作 Agent 与运行记录表")

    @app.cli.command("init-arxiv-daily")
    def init_arxiv_daily() -> None:
        """Create the academic daily run table and register the built-in Agent."""
        ArxivDailyRun.__table__.create(bind=db.engine, checkfirst=True)
        agent = db.session.scalar(select(Agent).where(Agent.code == ARXIV_DAILY_AGENT["code"]))
        if agent is None:
            agent = Agent(**ARXIV_DAILY_AGENT)
            db.session.add(agent)
        else:
            for key, value in ARXIV_DAILY_AGENT.items():
                setattr(agent, key, value)
        db.session.commit()
        click.echo("已初始化学术速递 Agent 与运行记录表")
