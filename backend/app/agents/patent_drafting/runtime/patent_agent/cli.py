from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from patent_agent.adapters.cnipa import CnipaAdapter
from patent_agent.adapters.qwen import QwenAdapter
from patent_agent.cnipa_benchmark import (
    BenchmarkFixtureAdapter,
    default_report_path,
    load_fixture_results,
    run_benchmark,
)
from patent_agent.config import load_config
from patent_agent.docx_qa import default_docx_qa_output, run_docx_qa
from patent_agent.doctor import run_doctor
from patent_agent.errors import ConfigurationError, PatentAgentError, SearchError
from patent_agent.review_workflow import prepare_revision_workspace, record_review
from patent_agent.runner import (
    PatentRunner,
    RunStore,
    status_payload,
    status_summary_payload,
)


def _common(parser: argparse.ArgumentParser, *, allow_fake: bool = True) -> None:
    parser.add_argument("--config", type=Path, default=None, help="YAML 配置文件路径")
    parser.add_argument("--env-file", type=Path, default=None, help="被 Git 忽略的 dotenv 文件路径")
    if allow_fake:
        parser.add_argument("--fake", action="store_true", help="使用确定性离线 Adapter")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m patent_agent",
        description="独立运行的本地专利交底书与权利要求草案 Agent",
        epilog=(
            "请从仓库根目录运行。新 Run 会在人工选择一个专利点时以状态码 10 暂停，"
            "随后可在新进程中 Resume。输出位于 runs/<run_id>/。Fake 模式只使用带明确标记的 "
            "Fixture；真实 CNIPA 不会自动降级，除非显式传入 --allow-cnipa-fixture-fallback。"
            "默认工作流模式为 flow_first；需要失败关闭时使用 --workflow-mode strict。"
            "状态码：0 成功；10 等待人工输入；20 配置/输入错误；21 模型错误；"
            "22 解析/合同错误；23 检索错误；24 阻断性质量错误；25 必需 DOCX 错误；70 内部错误。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor",
        help="检查本地依赖和 Qwen 连通性",
        description="检查 Python、Node、浏览器、导出依赖和 Qwen；--fake 为完全离线路径。",
    )
    _common(doctor)
    doctor.add_argument("--skip-qwen", action="store_true", help="跳过 Qwen 配置与连通性检查")
    doctor.add_argument("--live-cnipa", action="store_true", help="同时执行一次真实 CNIPA 查询")
    doctor.add_argument("--cnipa-query", default="缓存调度")

    run = sub.add_parser(
        "run",
        help="启动新 Case 并在专利点选择处暂停",
        description="快照 Case、生成候选、持久化人工中断，并以状态码 10 退出。",
    )
    _common(run)
    run.add_argument("--case", type=Path, required=True)
    run.add_argument(
        "--workflow-mode",
        choices=("flow_first", "strict"),
        default=None,
        help="运行策略；默认 flow_first，strict 保留失败关闭行为",
    )
    run.add_argument(
        "--allow-cnipa-fixture-fallback",
        action="store_true",
        help="仅用于明确演示：真实 CNIPA 零结果或失败且无记录时，使用带标记的测试 Fixture",
    )

    status = sub.add_parser("status", help="显示持久化 Run 状态与 Artifact 路径")
    _common(status, allow_fake=False)
    status.add_argument("--run-id", required=True)
    status.add_argument(
        "--summary",
        action="store_true",
        help="只显示耗时、检索、Claims、审核状态、Warning 和下一步",
    )

    resume = sub.add_parser(
        "resume",
        help="提交专利点选择并继续",
        description="校验唯一选中候选，并从持久化 Run 快照继续。",
    )
    _common(resume, allow_fake=False)
    resume.add_argument("--run-id", required=True)
    resume.add_argument(
        "--response",
        type=Path,
        default=None,
        help="首次提交专利点选择时必需；选择已持久化后的失败恢复可省略",
    )
    resume.add_argument(
        "--allow-cnipa-fixture-fallback",
        action="store_true",
        help="仅用于明确演示：真实 CNIPA 零结果或失败且无记录时，使用带标记的测试 Fixture",
    )

    revalidate = sub.add_parser(
        "revalidate",
        help="基于已完成 Run 创建不调用外部服务的修订复验 Run",
        description=(
            "复制父 Run 的不可变输入和带 Hash 的检索证据，按需载入修订后的"
            "结构化交底书、Claim Plan 或 Claims，只重新执行确定性检查、"
            "Claim–Evidence 审核表和导出。父 Run 不会被修改。"
        ),
    )
    _common(revalidate, allow_fake=False)
    revalidate.add_argument("--run-id", required=True, help="已完成的父 Run ID")
    revalidate.add_argument(
        "--disclosure-sections",
        type=Path,
        default=None,
        help="可选：修订后的 disclosure_sections JSON",
    )
    revalidate.add_argument(
        "--claim-plan",
        type=Path,
        default=None,
        help="可选：修订后的 claim_plan JSON",
    )
    revalidate.add_argument(
        "--claims",
        type=Path,
        default=None,
        help="可选：修订后的 claims JSON",
    )

    prepare_revision = sub.add_parser(
        "prepare-revision",
        help="从已完成 Run 生成不调用外部服务的可编辑修订工作区",
        description=(
            "校验父 Run Manifest 后，把结构化交底书、Claim Plan、Claims "
            "和 Claim–Evidence 审核表复制到被忽略的 outputs 目录，并生成"
            "离线 revalidate 命令。父 Run 不会被修改。"
        ),
    )
    _common(prepare_revision, allow_fake=False)
    prepare_revision.add_argument(
        "--run-id",
        required=True,
        help="已完成的父 Run ID",
    )
    prepare_revision.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="可选：配置 outputs_dir 内尚不存在的目标目录",
    )

    record_review_parser = sub.add_parser(
        "record-review",
        help="为已完成 Run 创建独立、带 Hash 的外部审核记录",
        description=(
            "校验审核文件与 Run 的输入/Claims Hash 和全部 feature 结论，"
            "在被忽略的 outputs 目录创建独立审核包。不会改写原 Run，"
            "也不会把 GPT 或普通人工审核冒充专利专业审核或法律意见。"
        ),
    )
    _common(record_review_parser, allow_fake=False)
    record_review_parser.add_argument(
        "--run-id",
        required=True,
        help="已完成的 Run ID",
    )
    record_review_parser.add_argument(
        "--review",
        type=Path,
        required=True,
        help="claim_evidence_decision_v1 JSON 文件",
    )
    record_review_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="可选：配置 outputs_dir 内尚不存在的目标目录",
    )

    smoke_qwen = sub.add_parser(
        "smoke-qwen",
        help="执行确定性的真实 Qwen JSON smoke",
        description="发起一次真实结构化输出请求；不会打印 API Key 或响应正文。",
    )
    _common(smoke_qwen, allow_fake=False)

    smoke_cnipa = sub.add_parser(
        "smoke-cnipa",
        help="执行一次真实 CNIPA 检索",
        description="使用 vendored CNIPA 工具。零结果是诚实的查询结果，不是新颖性结论。",
    )
    _common(smoke_cnipa, allow_fake=False)
    smoke_cnipa.add_argument("--query", default="缓存调度")

    benchmark_cnipa = sub.add_parser(
        "benchmark-cnipa",
        help="运行公开已知相关专利的 CNIPA Recall@K 基准",
        description=(
            "Fixture 只验证离线指标合同；--live 才会调用 vendored CNIPA。"
            "结果数量不等于召回率，报告也不形成新颖性或创造性结论。"
        ),
    )
    _common(benchmark_cnipa, allow_fake=False)
    benchmark_mode = benchmark_cnipa.add_mutually_exclusive_group(required=True)
    benchmark_mode.add_argument(
        "--fixture",
        action="store_true",
        help="使用明确标记的离线查询结果快照，不调用真实 CNIPA",
    )
    benchmark_mode.add_argument(
        "--live",
        action="store_true",
        help="逐条调用真实 vendored CNIPA 工具并保存外部状态",
    )
    benchmark_cnipa.add_argument(
        "--benchmark",
        type=Path,
        default=Path("benchmarks/cnipa_recall_v1.json"),
        help="公开已知相关专利基准 JSON",
    )
    benchmark_cnipa.add_argument(
        "--fixture-results",
        type=Path,
        default=Path("benchmarks/fixtures/cnipa_recall_fixture_v1.json"),
        help="Fixture 模式使用的查询结果快照 JSON",
    )
    benchmark_cnipa.add_argument(
        "--strategy",
        action="append",
        default=None,
        help="只运行指定 strategy_id；可重复传入，未指定时运行全部策略",
    )
    benchmark_cnipa.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="每条查询纳入召回统计的前 K 条记录，默认 3",
    )
    benchmark_cnipa.add_argument(
        "--output",
        type=Path,
        default=None,
        help="报告 JSON 路径；默认写入被 Git 忽略的 outputs/cnipa_benchmarks/",
    )

    qa_docx = sub.add_parser(
        "qa-docx",
        help="独立渲染 DOCX 并执行逐页版式质量检查",
        description=(
            "在 Runner 外把 DOCX 渲染为 PDF/逐页 PNG，检查中文字体证据、"
            "文本保留、空白页和页边溢出。未传 --review 时不会冒充人工视觉通过。"
        ),
    )
    _common(qa_docx, allow_fake=False)
    qa_docx.add_argument(
        "--docx",
        type=Path,
        required=True,
        help="待检查的 DOCX 文件",
    )
    qa_docx.add_argument(
        "--review",
        type=Path,
        default=None,
        help="可选：绑定 DOCX Hash 并覆盖全部页的 docx_visual_review_v1 JSON",
    )
    qa_docx.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="可选：被忽略的全新输出目录；默认 outputs/docx_qa/<timestamp>-<hash>/",
    )
    qa_docx.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="LibreOffice 和 Poppler 各自的超时秒数，默认 120",
    )

    return parser


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            config = load_config(config_path=args.config, env_file=args.env_file, require_model=False)
            payload = (
                status_summary_payload(config, args.run_id)
                if args.summary
                else status_payload(config, args.run_id)
            )
            _print(payload)
            return 0

        if args.command == "resume":
            provisional = load_config(config_path=args.config, env_file=args.env_file, require_model=False)
            state = RunStore(provisional, args.run_id).load()
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                fake_mode=state.get("provider_mode") == "fake",
                require_model=state.get("provider_mode") != "fake",
                workflow_mode=state.get("workflow_mode"),
            )
            code = PatentRunner(config).resume(args.run_id, args.response, allow_fixture_fallback=args.allow_cnipa_fixture_fallback)
            _print(status_payload(config, args.run_id))
            return code

        if args.command == "revalidate":
            provisional = load_config(
                config_path=args.config,
                env_file=args.env_file,
                require_model=False,
            )
            parent = RunStore(provisional, args.run_id).load()
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                fake_mode=parent.get("provider_mode") == "fake",
                require_model=False,
                workflow_mode=parent.get("workflow_mode"),
            )
            new_run_id, code = PatentRunner(config).revalidate(
                args.run_id,
                disclosure_sections_file=args.disclosure_sections,
                claim_plan_file=args.claim_plan,
                claims_file=args.claims,
            )
            _print(
                {
                    "run_id": new_run_id,
                    "parent_run_id": args.run_id,
                    "exit_code": code,
                    **status_payload(config, new_run_id),
                }
            )
            return code

        if args.command == "prepare-revision":
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                require_model=False,
            )
            _print(
                prepare_revision_workspace(
                    config,
                    args.run_id,
                    output_dir=args.output_dir,
                )
            )
            return 0

        if args.command == "record-review":
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                require_model=False,
            )
            _print(
                record_review(
                    config,
                    args.run_id,
                    args.review,
                    output_dir=args.output_dir,
                )
            )
            return 0

        if args.command == "qa-docx":
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                require_model=False,
            )
            output_dir = (
                args.output_dir
                if args.output_dir is not None
                else default_docx_qa_output(
                    config.outputs_dir,
                    args.docx.expanduser().resolve(),
                )
            )
            report = run_docx_qa(
                args.docx,
                output_dir=output_dir,
                allowed_output_root=config.outputs_dir,
                review_path=args.review,
                timeout=args.timeout,
            )
            _print(report)
            return 24 if report["status"] in {"failed", "rework"} else 0

        if args.command == "doctor":
            config = load_config(
                config_path=args.config,
                env_file=args.env_file,
                fake_mode=args.fake,
                require_model=False,
            )
            payload = run_doctor(config, skip_qwen=args.skip_qwen, live_cnipa=args.live_cnipa, cnipa_query=args.cnipa_query)
            _print(payload)
            return 0 if payload["status"] == "passed" else 20
        require_model = args.command not in {
            "smoke-cnipa",
            "benchmark-cnipa",
        }
        config = load_config(
            config_path=args.config,
            env_file=args.env_file,
            fake_mode=getattr(args, "fake", False),
            require_model=require_model,
            workflow_mode=getattr(args, "workflow_mode", None),
        )
        if args.command == "run":
            run_id, code = PatentRunner(config).start(args.case, fake=args.fake, allow_fixture_fallback=args.allow_cnipa_fixture_fallback)
            _print({"run_id": run_id, "exit_code": code, **status_payload(config, run_id)})
            return code
        if args.command == "smoke-qwen":
            _print(QwenAdapter(config.qwen).smoke_test())
            return 0
        if args.command == "smoke-cnipa":
            result = CnipaAdapter(config.cnipa).search(args.query)
            _print(result.to_dict())
            if result.status not in {"success", "zero_results"}:
                raise SearchError(f"CNIPA smoke failed with status {result.status}: {result.error_message or 'no detail'}")
            return 0
        if args.command == "benchmark-cnipa":
            benchmark_path = args.benchmark.expanduser().resolve()
            mode = "fixture" if args.fixture else "real_cnipa"
            fixture_path = (
                args.fixture_results.expanduser().resolve()
                if args.fixture
                else None
            )
            adapter = (
                BenchmarkFixtureAdapter(load_fixture_results(fixture_path))
                if fixture_path is not None
                else CnipaAdapter(config.cnipa)
            )
            output_path = (
                args.output.expanduser().resolve()
                if args.output is not None
                else default_report_path(config.outputs_dir, mode=mode)
            )
            report = run_benchmark(
                benchmark_path,
                adapter,
                mode=mode,
                top_k=args.top_k,
                fixture_path=fixture_path,
                output_path=output_path,
                selected_strategy_ids=set(args.strategy or ()),
            )
            _print(
                {
                    "status": "benchmark_completed",
                    "mode": report["mode"],
                    "measurement_status": report["measurement_status"],
                    "external_complete": report["external_complete"],
                    "top_k_per_query": report["top_k_per_query"],
                    "selected_strategy_ids": report["selected_strategy_ids"],
                    "aggregates": report["aggregates"],
                    "output_path": report["output_path"],
                }
            )
            return 0
        raise ConfigurationError(f"unknown command: {args.command}")
    except PatentAgentError as exc:
        _print({"status": "error", "error_type": type(exc).__name__, "message": str(exc), "exit_code": exc.exit_code})
        return exc.exit_code
    except KeyboardInterrupt:
        _print({"status": "error", "error_type": "KeyboardInterrupt", "exit_code": 130})
        return 130
    except Exception as exc:
        _print({"status": "error", "error_type": type(exc).__name__, "message": str(exc), "exit_code": 70})
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
