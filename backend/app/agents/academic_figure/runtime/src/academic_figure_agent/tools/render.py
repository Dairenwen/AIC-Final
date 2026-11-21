from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from config.settings import Settings, get_settings


class FigureRenderInput(BaseModel):
    python_file: str
    output_dir: str


class FigureRenderTool(BaseTool):
    name: str = "figure_render"
    description: str = "Execute agent-generated, template-controlled Python plotting code with a timeout."
    args_schema: type[BaseModel] = FigureRenderInput

    def _run(self, python_file: str, output_dir: str) -> dict[str, object]:
        return execute_python_renderer(Path(python_file), Path(output_dir))


def execute_python_renderer(
    python_file: Path,
    output_dir: Path,
    settings: Settings | None = None,
) -> dict[str, object]:
    resolved = settings or get_settings()
    script = python_file.resolve()
    allowed_dir = output_dir.resolve()
    if script.parent != allowed_dir or script.name != "figure.py":
        raise ValueError("Only the generated figure.py inside the current output directory may be executed")
    env = os.environ.copy()
    source_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(source_root), env.get("PYTHONPATH", "")]))
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=allowed_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=resolved.code_execution_timeout_seconds,
        check=False,
    )
    result = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    (allowed_dir / "execution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Figure rendering failed: {completed.stderr[-1000:]}")
    return result
