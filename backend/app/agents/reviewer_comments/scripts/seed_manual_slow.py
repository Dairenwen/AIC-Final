"""写入 SLOW 冒烟评测种子：工作区 + 三审稿人意见 + 论文冒烟解析。

用法（langgraph-agent 目录、已激活 venv、已配置 .env）：

    python scripts/init_db.py
    python scripts/seed_manual_slow.py
    python scripts/manual_e2e_slow.py --auto-approve

可选：
    python scripts/seed_manual_slow.py --pdf "C:\\Users\\stf\\Desktop\\1706.03762v7.pdf"
    python scripts/seed_manual_slow.py --max-pages 3
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path
from uuid import UUID, uuid4, uuid5

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from sqlalchemy import text

from langgraph_agent.adapters.postgres.db import create_session_factory
from langgraph_agent.adapters.postgres.models.manuscript import ManuscriptVersion
from langgraph_agent.adapters.postgres.models.review import ReviewInput, ReviewParty
from langgraph_agent.adapters.postgres.models.workspace import Workspace
from langgraph_agent.adapters.postgres.repositories.manuscript_repo import (
    ManuscriptRepository,
)
from langgraph_agent.adapters.postgres.repositories.paper_card_repo import (
    PaperCardRepository,
)
from langgraph_agent.adapters.postgres.repositories.suggestion_repo import (
    default_response_settings,
)
from langgraph_agent.tools.paper_card import generate_rule_based_paper_cards
from langgraph_agent.tools.paper_schemas import (
    ParsedPaper,
    PaperSection,
    SectionType,
)

# 与 FAST demo 隔离的固定 ID，便于反复复现
WS = UUID("00000000-0000-4000-8000-000000000010")
USER = "demo-user-slow"
FIXTURE_DIR = _ROOT / "assets" / "examples" / "slow_smoke"
DEFAULT_PDF = FIXTURE_DIR / "attention_is_all_you_need.pdf"
REVIEW_FILE = FIXTURE_DIR / "review_comments.txt"
STORAGE_DIR = _ROOT / ".storage" / "manuscripts" / str(WS)

_PARTY_HEADER = re.compile(
    r"^\s*审稿人\s*(?P<num>\d+)\s*$",
    re.MULTILINE,
)


def _wipe_workspace(session, workspace_id: UUID) -> None:
    """尽量清空同 workspace 下业务表，允许重复 seed。"""
    existing = {
        str(row[0])
        for row in session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
        ).fetchall()
    }
    statements = [
        (
            "reply_drafts",
            "DELETE FROM reply_drafts WHERE reply_id IN "
            "(SELECT reply_id FROM source_replies WHERE workspace_id = :w)",
        ),
        ("source_replies", "DELETE FROM source_replies WHERE workspace_id = :w"),
        ("modification_facts", "DELETE FROM modification_facts WHERE workspace_id = :w"),
        ("analysis_snapshots", "DELETE FROM analysis_snapshots WHERE workspace_id = :w"),
        ("suggestion_sources", "DELETE FROM suggestion_sources WHERE workspace_id = :w"),
        ("suggestions", "DELETE FROM suggestions WHERE workspace_id = :w"),
        ("paper_cards", "DELETE FROM paper_cards WHERE workspace_id = :w"),
        ("manuscript_versions", "DELETE FROM manuscript_versions WHERE workspace_id = :w"),
        ("review_inputs", "DELETE FROM review_inputs WHERE workspace_id = :w"),
        ("review_parties", "DELETE FROM review_parties WHERE workspace_id = :w"),
        ("graph_runs", "DELETE FROM graph_runs WHERE workspace_id = :w"),
        ("workspaces", "DELETE FROM workspaces WHERE workspace_id = :w"),
    ]
    for table_name, sql in statements:
        # reply_drafts 依赖 source_replies 表存在
        needed = {table_name}
        if table_name == "reply_drafts":
            needed.add("source_replies")
        if not needed.issubset(existing):
            continue
        session.execute(text(sql), {"w": workspace_id})
    session.commit()


def _split_reviewers(raw: str) -> list[tuple[str, str]]:
    """返回 [(display_name, body), ...]。"""
    text_body = raw.replace("\r\n", "\n").strip()
    matches = list(_PARTY_HEADER.finditer(text_body))
    if not matches:
        return [("Reviewer 1", text_body)]
    parties: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text_body)
        body = text_body[start:end].strip()
        # 去掉开头的「审稿意见：」
        body = re.sub(r"^\s*审稿意见\s*[:：]\s*", "", body).strip()
        name = f"审稿人 {match.group('num')}"
        if body:
            parties.append((name, body))
    return parties or [("Reviewer 1", text_body)]


def _minimal_transformer_smoke_paper(pdf_path: Path, reason: str) -> ParsedPaper:
    """无 PDF 解析库时的最小冒烟结构，保证 SLOW 链路可继续。"""
    sections = [
        PaperSection(
            original_heading="Abstract",
            normalized_type=SectionType.ABSTRACT,
            text=(
                "The dominant sequence transduction models are based on complex recurrent or "
                "convolutional neural networks. We propose the Transformer, a model architecture "
                "eschewing recurrence and instead relying entirely on an attention mechanism."
            ),
            pages=[1],
            confidence=0.9,
            section_id="smoke-section-0001",
            parent_id=None,
            order_index=0,
            level=1,
            excerpt="We propose the Transformer, relying entirely on attention.",
        ),
        PaperSection(
            original_heading="3.2.2 Multi-Head Attention",
            normalized_type=SectionType.METHOD,
            text=(
                "Instead of performing a single attention function, we found it beneficial to "
                "linearly project the queries, keys and values h times. In this work we employ "
                "h = 8 parallel attention layers, or heads."
            ),
            pages=[3],
            confidence=0.8,
            section_id="smoke-section-0002",
            parent_id=None,
            order_index=1,
            level=2,
            excerpt="we employ h = 8 parallel attention layers, or heads.",
        ),
        PaperSection(
            original_heading="5.1 Training Data and Batching",
            normalized_type=SectionType.DATASET,
            text=(
                "We trained on the standard WMT 2014 English-German dataset consisting of about "
                "4.5 million sentence pairs, and on the larger WMT 2014 English-French dataset."
            ),
            pages=[7],
            confidence=0.8,
            section_id="smoke-section-0003",
            parent_id=None,
            order_index=2,
            level=2,
            excerpt="WMT 2014 English-German dataset consisting of about 4.5 million sentence pairs.",
        ),
        PaperSection(
            original_heading="6 Results",
            normalized_type=SectionType.RESULTS,
            text=(
                "On the WMT 2014 English-to-German translation task, the big transformer model "
                "outperforms the best previously reported models. Training is also substantially faster."
            ),
            pages=[8],
            confidence=0.75,
            section_id="smoke-section-0004",
            parent_id=None,
            order_index=3,
            level=1,
            excerpt="the big transformer model outperforms previously reported models.",
        ),
    ]
    return ParsedPaper(
        title="Attention Is All You Need",
        abstract=sections[0].text,
        full_text="\n\n".join(section.text for section in sections),
        sections=sections,
        parse_warnings=[
            f"SMOKE_MINIMAL_STRUCTURE: {reason}",
            f"SMOKE_MINIMAL_STRUCTURE: 未真正读取 PDF 正文（path={pdf_path.name}）",
        ],
    )


def _smoke_parse_pdf(pdf_path: Path, *, max_pages: int) -> ParsedPaper:
    """冒烟解析：优先完整 parse_pdf；否则 PyMuPDF 抽前 N 页；再否则最小结构。"""
    # 1) 尝试完整解析器（若装了 pymupdf4llm）
    try:
        from langgraph_agent.tools.pdf_parse import parse_pdf

        paper = parse_pdf(pdf_path)
        if paper.full_text.strip() or paper.sections:
            # 冒烟：截断章节，避免后续卡片/证据过重
            limited_sections = paper.sections[:12]
            warnings = list(paper.parse_warnings) + [
                f"SMOKE_PARSE: 仅保留前 {len(limited_sections)} 个章节用于冒烟"
            ]
            return ParsedPaper(
                title=paper.title or pdf_path.stem,
                abstract=paper.abstract,
                full_text=paper.full_text[:20000],
                sections=limited_sections,
                parse_warnings=warnings,
            )
    except Exception as error:  # noqa: BLE001
        full_parser_error = f"{type(error).__name__}: {error}"
    else:
        full_parser_error = "完整解析无正文"

    # 2) 降级：PyMuPDF 抽前 N 页纯文本
    try:
        import pymupdf
    except ImportError as error:
        return _minimal_transformer_smoke_paper(
            pdf_path,
            f"pymupdf 不可用（{error}）；{full_parser_error}",
        )

    document = pymupdf.open(str(pdf_path))
    try:
        page_count = min(max_pages, document.page_count)
        page_texts: list[str] = []
        for page_index in range(page_count):
            page_texts.append(document.load_page(page_index).get_text("text") or "")
        joined = "\n\n".join(page_texts).strip()
        if not joined:
            return _minimal_transformer_smoke_paper(
                pdf_path,
                f"PDF 前 {page_count} 页无文本；{full_parser_error}",
            )
        # 粗切：按空行分成若干“伪章节”
        blocks = [block.strip() for block in re.split(r"\n\s*\n", joined) if block.strip()]
        title = blocks[0][:200] if blocks else pdf_path.stem
        abstract = ""
        for block in blocks[:8]:
            if re.search(r"abstract|摘要", block, re.I):
                abstract = block[:1200]
                break
        if not abstract and len(blocks) > 1:
            abstract = blocks[1][:1200]

        sections: list[PaperSection] = []
        for index, block in enumerate(blocks[:10]):
            heading = block.splitlines()[0][:120] if block else f"Section {index + 1}"
            sections.append(
                PaperSection(
                    original_heading=heading,
                    normalized_type=SectionType.OTHER,
                    text=block[:3000],
                    pages=[min(index + 1, page_count)],
                    confidence=0.4,
                    section_id=f"smoke-section-{index + 1:04d}",
                    parent_id=None,
                    order_index=index,
                    level=1,
                    excerpt=block[:400],
                )
            )
        return ParsedPaper(
            title=title,
            abstract=abstract,
            full_text=joined[:20000],
            sections=sections,
            parse_warnings=[
                f"SMOKE_PARSE: 仅解析前 {page_count}/{document.page_count} 页",
                f"SMOKE_PARSE_FALLBACK: {full_parser_error}",
            ],
        )
    finally:
        document.close()


def _to_structure_summary(paper: ParsedPaper) -> dict:
    """契约：full_text 不进 JSONB。"""
    return {
        "title": paper.title or "",
        "abstract": paper.abstract or "",
        "sections": [
            {
                "section_id": section.section_id or f"section-{index + 1:04d}",
                "parent_id": section.parent_id,
                "order_index": (
                    section.order_index if section.order_index is not None else index
                ),
                "original_heading": section.original_heading,
                "normalized_type": section.normalized_type.value,
                "level": section.level if section.level is not None else 1,
                "pages": list(section.pages),
                "confidence": section.confidence,
                "excerpt": section.excerpt or section.text[:400],
            }
            for index, section in enumerate(paper.sections)
        ],
        "parse_warnings": list(paper.parse_warnings),
        "processing": {"stage": "smoke_seed", "mode": "SLOW_SMOKE"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SLOW 冒烟种子数据")
    parser.add_argument(
        "--pdf",
        type=str,
        default=str(DEFAULT_PDF),
        help="论文 PDF 路径（默认 assets/examples/slow_smoke 内副本）",
    )
    parser.add_argument("--max-pages", type=int, default=3, help="冒烟解析最多页数")
    parser.add_argument(
        "--reviews",
        type=str,
        default=str(REVIEW_FILE),
        help="审稿意见文本路径",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        raise SystemExit(f"PDF 不存在：{pdf_path}")
    review_path = Path(args.reviews)
    if not review_path.is_file():
        raise SystemExit(f"审稿意见文件不存在：{review_path}")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    # 若用户指定了外部 PDF，同步一份到 fixture 目录便于复现
    fixture_pdf = FIXTURE_DIR / "attention_is_all_you_need.pdf"
    if pdf_path.resolve() != fixture_pdf.resolve():
        shutil.copy2(pdf_path, fixture_pdf)
        print(f"[信息] 已复制 PDF 到复现目录：{fixture_pdf}")

    raw_reviews = review_path.read_text(encoding="utf-8")
    parties = _split_reviewers(raw_reviews)
    print(f"[信息] 解析到 {len(parties)} 位审稿人")

    print(f"[信息] 冒烟解析 PDF：{pdf_path} (max_pages={args.max_pages})")
    paper = _smoke_parse_pdf(pdf_path, max_pages=max(1, args.max_pages))
    summary = _to_structure_summary(paper)
    print(
        f"[信息] 解析结果 title={paper.title[:60]!r} "
        f"sections={len(paper.sections)} warnings={len(paper.parse_warnings)}"
    )
    for warning in paper.parse_warnings[:5]:
        print(f"  - {warning}")

    pdf_bytes = pdf_path.read_bytes()
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored_pdf = STORAGE_DIR / f"{content_hash[:16]}.pdf"
    if not stored_pdf.is_file():
        stored_pdf.write_bytes(pdf_bytes)
    storage_uri = str(stored_pdf.resolve())

    # 规则卡片（不调 LLM），供 CONFIRM_BASELINE 冒烟
    rule_cards = generate_rule_based_paper_cards(paper)
    print(f"[信息] 规则基线卡片数 = {len(rule_cards)}")

    sf = create_session_factory()
    with sf() as session:
        _wipe_workspace(session, WS)

        session.add(
            Workspace(
                workspace_id=WS,
                user_id=USER,
                title="slow-smoke-demo",
                mode="SLOW",
                status="ACTIVE",
                global_settings=default_response_settings(),
            )
        )
        session.flush()

        party_ids: list[UUID] = []
        for index, (display_name, body) in enumerate(parties, start=1):
            party_id = uuid5(WS, f"slow-party-{index}")
            party_ids.append(party_id)
            session.add(
                ReviewParty(
                    party_id=party_id,
                    workspace_id=WS,
                    role="REVIEWER",
                    display_name=display_name,
                    raw_label=f"R{index}",
                )
            )
        session.flush()

        for index, ((display_name, body), party_id) in enumerate(
            zip(parties, party_ids), start=1
        ):
            session.add(
                ReviewInput(
                    review_input_id=uuid4(),
                    workspace_id=WS,
                    party_id=party_id,
                    version_no=1,
                    raw_text=body,
                    storage_uri=None,
                    content_hash=hashlib.sha256(
                        f"{display_name}:{body}".encode("utf-8")
                    ).hexdigest(),
                    language="zh",
                    is_current=True,
                )
            )
        session.flush()

        manuscript_repo = ManuscriptRepository()
        manuscript = manuscript_repo.create(
            session,
            workspace_id=WS,
            version_no=1,
            source_type="UPLOAD",
            storage_uri=storage_uri,
            content_hash=content_hash,
            parse_status="SUCCEEDED",
            structure_summary=summary,
            is_baseline=False,
        )
        session.flush()

        if rule_cards:
            card_repo = PaperCardRepository()
            card_repo.bulk_create(
                session,
                WS,
                manuscript.manuscript_version_id,
                [card.to_dict() for card in rule_cards],
            )
        session.commit()
        manuscript_version_id = manuscript.manuscript_version_id

    # 回写 sample json，方便 e2e 读取
    sample_json = _ROOT / "assets" / "examples" / "sample_task_init_slow.json"
    sample_json.write_text(
        (
            "{\n"
            f'  "workspace_id": "{WS}",\n'
            f'  "user_id": "{USER}",\n'
            '  "mode": "SLOW",\n'
            f'  "manuscript_version_id": "{manuscript_version_id}",\n'
            '  "input_version": null\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    print("[成功] SLOW 冒烟种子已写入")
    print(f"  workspace_id           = {WS}")
    print(f"  user_id                = {USER}")
    print(f"  manuscript_version_id  = {manuscript_version_id}")
    print(f"  storage_uri            = {storage_uri}")
    print(f"  parties                = {len(parties)}")
    print(f"  sample json            = {sample_json}")
    print()
    print("下一步：")
    print("  python scripts/manual_e2e_slow.py --auto-approve")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"[失败] {type(error).__name__}: {error}", file=sys.stderr)
        raise
