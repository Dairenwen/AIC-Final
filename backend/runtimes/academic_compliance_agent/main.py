from __future__ import annotations

import argparse
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from academic_compliance_agent.app.graph.workflow import run_compliance_workflow


PACKAGE_ROOT = Path(__file__).resolve().parent


def load_local_env(env_path: Path | None = None) -> None:
    env_path = env_path or (PACKAGE_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def build_initial_state(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "user_id": args.user_id,
        "thread_id": args.thread_id,
        "task_type": args.task_type,
        "target_rule_set": args.target_rule_set,
        "files": [
            {
                "file_type": "manuscript",
                "path": str(Path(args.input).resolve()),
            }
        ],
    }


def write_outputs(result: Dict[str, Any], output_dir: Path) -> Dict[str, str]:
    if not output_dir.is_absolute():
        output_dir = PACKAGE_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = result.get("task_id", "TASK")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    report_path = output_dir / f"{task_id}_{timestamp}_report.md"
    json_path = output_dir / f"{task_id}_{timestamp}_result.json"

    report_path.write_text(result.get("final_report", ""), encoding="utf-8")
    json_path.write_text(
        json.dumps(result.get("structured_output", result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"report": str(report_path), "json": str(json_path)}


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description="Run the academic compliance checking agent.")
    parser.add_argument("--input", required=True, help="Path to a Markdown, TXT, or DOCX manuscript.")
    parser.add_argument("--user-id", default=os.getenv("COMPLIANCE_AGENT_USER_ID", "default_user"), help="User id for long-term memory.")
    parser.add_argument("--thread-id", default=os.getenv("COMPLIANCE_AGENT_THREAD_ID", "default_thread"), help="Thread id for short-term memory.")
    parser.add_argument("--task-type", default="paper_precheck", help="Task type, such as paper_precheck or journal_submission.")
    parser.add_argument("--target-rule-set", default=os.getenv("COMPLIANCE_AGENT_RULE_SET", "default"), help="Rule set name.")
    parser.add_argument(
        "--output-dir",
        default=os.getenv("COMPLIANCE_AGENT_OUTPUT_DIR", "output/compliance_agent"),
        help="Directory for generated reports.",
    )
    args = parser.parse_args()

    result = run_compliance_workflow(build_initial_state(args))
    paths = write_outputs(result, Path(args.output_dir))
    summary = result.get("structured_output", {}).get("summary", {})

    print("Academic compliance check completed.")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.get("compliance_summary"):
        print(json.dumps(result["compliance_summary"], ensure_ascii=False, indent=2))
    print(f"Report: {paths['report']}")
    print(f"JSON: {paths['json']}")


if __name__ == "__main__":
    main()
