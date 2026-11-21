"""投稿推荐 Agent — 演示脚本"""
import asyncio, json, sys, io
from pathlib import Path

# 修复 Windows GBK 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent))
from agent import recommend_submission

SAMPLE_PAPER = {
    "title": "Dynamic Routing Mechanism for Cross-Modal Transfer Learning",
    "abstract": "We propose a novel dynamic routing mechanism that enables efficient cross-modal transfer learning...",
    "keywords": ["transfer learning", "cross-modal learning", "dynamic routing", "representation learning"],
    "references": [
        {"title": "Attention Is All You Need", "venue": "NeurIPS", "year": 2017},
        {"title": "BERT: Pre-training of Deep Bidirectional Transformers", "venue": "NAACL", "year": 2019},
        {"title": "ViT: An Image is Worth 16x16 Words", "venue": "ICLR", "year": 2021},
        {"title": "CLIP: Learning Transferable Visual Models", "venue": "ICML", "year": 2021},
        {"title": "Deep Residual Learning", "venue": "CVPR", "year": 2016},
        {"title": "GANs", "venue": "NeurIPS", "year": 2014},
        {"title": "Adam Optimizer", "venue": "ICLR", "year": 2015},
        {"title": "Dropout", "venue": "JMLR", "year": 2014},
        {"title": "Batch Normalization", "venue": "ICML", "year": 2015},
        {"title": "GPT-3", "venue": "NeurIPS", "year": 2020},
        {"title": "SimCLR", "venue": "ICML", "year": 2020},
        {"title": "MoCo", "venue": "CVPR", "year": 2020},
        {"title": "EfficientNet", "venue": "ICML", "year": 2019},
    ],
}

SAMPLE_QUALITY = {"experiment_completeness": 0.72, "novelty_level": "substantial",
                  "theoretical_rigor": 0.85, "writing_quality": 0.80, "innovation_score": 0.82}
SAMPLE_PREFS = {"target_ccf_levels": ["CCF-A", "CCF-B"], "max_review_weeks": 12, "prefer_oa": True}


async def main():
    print("=" * 70)
    print("  智研 · 投稿推荐 Agent — 演示")
    print("=" * 70)
    print(f"\n论文: {SAMPLE_PAPER['title']}")
    print("参数: 创新层次=substantial, 实验完整度=0.72, 偏好=CCF-A/B, OA, <=12周")
    print("\n正在运行投稿推荐工作流...\n" + "-" * 70)

    result = await recommend_submission(paper_id="DEMO-001", parsed_paper=SAMPLE_PAPER,
                                        quality_estimate=SAMPLE_QUALITY, user_preferences=SAMPLE_PREFS)

    print("\n" + "=" * 70)
    print("  推荐结果摘要")
    print("=" * 70)
    if result.get("errors"): print(f"\n错误: {result['errors']}")

    # 展示思考过程
    trace = result.get("thinking_trace", [])
    if trace:
        print(f"\n  --- 思考过程 ({len(trace)} 步) ---")
        for i, t in enumerate(trace, 1):
            print(f"\n  [{i}/{len(trace)}] {t['label']}: {t['summary']}")
            details = t.get("details", {})
            if t["step"] == "extract_features" and details:
                print(f"    领域: {details.get('sub_fields', [])}")
                print(f"    方法: {details.get('methodology_paradigm', '?')}")
                print(f"    创新: {details.get('novelty_level', '?')} | 实验完整度: {details.get('experiment_completeness', '?')}")
                if details.get("innovation_summary"):
                    print(f"    创新点: {details['innovation_summary']}")
            elif t["step"] == "semantic_match" and details:
                for m in details.get("top3_matches", []):
                    print(f"    {m['abbrev']}: overall={m['overall']:.0%} topic={m['topic']:.0%} method={m['methodology']:.0%} novelty={m['novelty_fit']:.0%}")
            elif t["step"] == "rank_and_recommend" and details:
                print(f"    公式: {details.get('ranking_formula', '')}")
                tr = details.get("top_recommendation")
                if tr:
                    print(f"    首选: {tr['abbrev']} [{tr['tier']}] CCF={tr['ccf']} 匹配={tr['match_overall']:.0%} 录用概率={tr['acceptance_prob']}")
                    for s in tr.get("strengths", []): print(f"      + {s}")
                    for r in tr.get("risks", []): print(f"      - {r}")
            elif t["step"] == "generate_strategy" and details:
                print(f"    主攻: {details.get('primary_target', '?')}")
                for tl in details.get("timeline", []):
                    print(f"    {tl.get('deadline','')} ({tl.get('days','?')}天后) -- {tl.get('venue','')}")
                print(f"    备选: {details.get('fallback_plan', '')[:120]}")

    recs = result.get("recommendations", [])
    if recs:
        tiers = {"sprint": "冲刺", "match": "匹配", "safety": "保底"}
        print(f"\n共推荐 {len(recs)} 个投稿目标:\n")
        for i, rec in enumerate(recs, 1):
            venue = rec.get("venue", {})
            ms = rec.get("match_score", {})
            print(f"  {i}. [{tiers.get(rec.get('tier', ''), rec.get('tier', ''))}] "
                  f"{venue.get('abbreviation', '')} ({venue.get('ccf_level', '')})")
            print(f"     综合匹配度: {ms.get('overall', 0):.2%} | 截稿: {venue.get('next_deadline', 'N/A')} | "
                  f"录用概率: {rec.get('estimated_acceptance_prob', 'N/A')}")
            if rec.get("strengths"): print(f"     优势: {rec['strengths'][0]}")
            if rec.get("risks"): print(f"     风险: {rec['risks'][0]}")
            print()

    checklist = result.get("submission_checklist", {})
    if checklist:
        print("-" * 70 + "\n  投稿准备清单\n" + "-" * 70)
        for item in checklist.get("format_checks", [])[:3]: print(f"  格式: {item}")
        for item in checklist.get("experiment_supplements", [])[:3]: print(f"  实验: {item}")
        for item in checklist.get("cover_letter_points", [])[:3]: print(f"  Cover: {item}")

    strategy = result.get("submission_strategy", {})
    if strategy:
        print("\n" + "-" * 70 + "\n  投稿策略\n" + "-" * 70)
        for t in strategy.get("timeline", [])[:5]:
            print(f"  截止: {t.get('deadline', '')} ({t.get('days_remaining', 'N/A')}天后) -- {t.get('venue', '')}")

    report = result.get("final_report", "")
    output_path = None
    if report:
        output_path = Path(__file__).parent / "output" / f"report_{result['task_id']}.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n报告已保存: {output_path}")
    else:
        print(f"\n报告生成失败: {result.get('errors', [])}")

    json_path = Path(__file__).parent / "output" / f"result_{result['task_id']}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in result.items() if k != "final_report"}
    slim["final_report_saved_to"] = str(output_path) if output_path else "FAILED"
    json_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"结构化结果已保存: {json_path}")
    print("\n" + "=" * 70 + "\n  演示完成！\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
