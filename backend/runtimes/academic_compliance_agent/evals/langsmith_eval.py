from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Set

from langsmith import Client
from langsmith.evaluation import run_evaluator

from academic_compliance_agent.app.graph.workflow import run_compliance_workflow
from academic_compliance_agent.app.services.llm import PLACEHOLDER_MARKERS, load_local_env


CASES: List[Dict[str, Any]] = [
    {
        "inputs": {
            "text": """# 基于大语言模型的科研文本辅助分析方法

摘要：本文提出一种面向科研文本的分析方法。

关键词：科研文本

## 引言

已有研究表明，该技术能够提升文献整理效率[1]。

## 方法

本文构建文本特征抽取流程。

图1 研究流程

## 结果

如图2所示，系统表现稳定。

## 参考文献

[1] 张三. 科研文本分析研究. 情报学报, 2024.
""",
        },
        "outputs": {
            "expected_risk_types": ["FIGURE_MENTION_WITHOUT_CAPTION", "KEYWORD_COUNT_OUT_OF_RANGE"],
        },
    },
    {
        "inputs": {
            "text": """# 遥感图像小目标检测方法研究

摘要：本文提出一种遥感图像小目标检测方法。目的、方法、结果和结论均在摘要中概述。

关键词：遥感图像；小目标检测；深度学习

## 引言

遥感图像小目标检测具有重要应用价值[1][3]。

## 方法

本文构建多尺度特征融合网络。

## 结果

图1 实验结果对比

表1

## 结论

本文方法可以提升检测效果。

## 参考文献

[1] Li X. Small object detection in remote sensing images. Remote Sensing, 2023.
[2] TODO.
""",
        },
        "outputs": {
            "expected_risk_types": ["CITED_REFERENCE_MISSING", "SUSPICIOUS_REFERENCE_PLACEHOLDER"],
        },
    },
]


def agent_target(inputs: Dict[str, Any]) -> Dict[str, Any]:
    result = run_compliance_workflow(
        {
            "task_type": "langsmith_eval",
            "input_text": inputs["text"],
        }
    )
    risks = result.get("risks", [])
    suggestions = result.get("suggestions", [])
    return {
        "summary": result.get("structured_output", {}).get("summary", {}),
        "risk_types": [risk.get("type") for risk in risks],
        "risk_count": len(risks),
        "suggestion_count": len(suggestions),
        "final_report": result.get("final_report", ""),
    }


@run_evaluator
def expected_risk_types(run, example):
    expected: Set[str] = set((example.outputs or {}).get("expected_risk_types", []))
    actual: Set[str] = set((run.outputs or {}).get("risk_types", []))
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return {
        "key": "expected_risk_types",
        "score": 1.0 if not missing else 0.0,
        "comment": f"missing={missing}; extra={extra[:8]}",
    }


@run_evaluator
def suggestions_present(run, example):
    count = int((run.outputs or {}).get("suggestion_count", 0))
    return {
        "key": "suggestions_present",
        "score": 1.0 if count > 0 else 0.0,
        "comment": f"suggestion_count={count}",
    }


def ensure_dataset(client: Client, dataset_name: str) -> None:
    existing = list(client.list_datasets(dataset_name=dataset_name, limit=1))
    if not existing:
        client.create_dataset(
            dataset_name,
            description="MVP evaluation dataset for the academic compliance checking agent.",
        )
        client.create_examples(dataset_name=dataset_name, examples=CASES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LangSmith evaluation for the academic compliance agent.")
    parser.add_argument("--dataset-name", default="academic-compliance-agent-mvp")
    parser.add_argument("--experiment-prefix", default="academic-compliance-agent")
    parser.add_argument("--no-create-dataset", action="store_true")
    args = parser.parse_args()

    load_local_env()
    langsmith_api_key = os.getenv("LANGSMITH_API_KEY", "")
    if not langsmith_api_key or any(marker in langsmith_api_key for marker in PLACEHOLDER_MARKERS):
        raise SystemExit(
            "Please set LANGSMITH_API_KEY in .env before running this evaluation. "
            "Open .env and replace the placeholder with your real LangSmith API key."
        )

    client = Client()
    if not args.no_create_dataset:
        ensure_dataset(client, args.dataset_name)

    results = client.evaluate(
        agent_target,
        data=args.dataset_name,
        evaluators=[expected_risk_types, suggestions_present],
        experiment_prefix=args.experiment_prefix,
        description="Academic compliance agent MVP evaluation.",
        max_concurrency=1,
    )
    print(results)


if __name__ == "__main__":
    main()
