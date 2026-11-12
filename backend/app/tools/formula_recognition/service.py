from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from time import perf_counter, monotonic

from flask import Flask


class FormulaRecognitionNotReady(RuntimeError):
    pass


class FormulaRecognitionFailed(RuntimeError):
    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class FormulaRecognitionService:
    def __init__(self, app: Flask):
        self.app = app
        self._inference_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_cache: dict[str, object] | None = None
        self._status_cache_at = 0.0

    def status(self) -> dict[str, object]:
        cache_seconds = max(
            0.0, float(self.app.config.get("FORMULA_RUNTIME_STATUS_CACHE_SECONDS", 30))
        )
        with self._status_lock:
            if self._status_cache is not None and monotonic() - self._status_cache_at < cache_seconds:
                return dict(self._status_cache)

        root = Path(self.app.config["FORMULA_RECOGNITION_ROOT"])
        python = Path(self.app.config["FORMULA_RECOGNITION_PYTHON"])
        script = root / "recognize.py"
        model = root / "unimernet" / "models" / "unimernet_base" / "pytorch_model.pth"
        environment_root = python.parent.parent
        site_packages = [
            environment_root / "Lib" / "site-packages",
            *environment_root.glob("lib/python*/site-packages"),
        ]
        pyvenv_cfg = python.parent.parent / "pyvenv.cfg"
        isolated = True
        if pyvenv_cfg.is_file():
            isolated = "include-system-site-packages = true" not in pyvenv_cfg.read_text(
                encoding="utf-8", errors="replace"
            ).lower()
        dependency_ready = False
        dependency_detail = "运行时解释器尚未存在"
        dependency_command = [
            str(python),
            "-c",
            "import torch, torchvision, transformers, unimernet",
        ]
        if python.is_file():
            try:
                probe = subprocess.run(
                    dependency_command,
                    cwd=root,
                    env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"},
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                dependency_ready = probe.returncode == 0
                if not dependency_ready:
                    dependency_detail = (
                        "\n".join(line for line in probe.stderr.splitlines() if line.strip())
                        or "运行时依赖导入失败"
                    )[-1000:]
                else:
                    dependency_detail = "torch、torchvision、transformers、unimernet 均可导入"
            except (OSError, subprocess.TimeoutExpired) as exc:
                dependency_detail = f"依赖探测失败：{exc}"

        checks = {
            "runtime": script.is_file(),
            "pythonEnvironment": python.is_file(),
            "model": model.is_file(),
            "isolatedEnvironment": isolated,
            "huggingfaceHub": any((path / "huggingface_hub" / "__init__.py").is_file() for path in site_packages),
            "inferenceDependencies": dependency_ready,
        }
        result = {
            "ready": all(checks.values()),
            "checks": checks,
            "device": str(self.app.config["FORMULA_RECOGNITION_DEVICE"]),
            "python": str(python),
            "dependencyDetail": dependency_detail,
        }
        with self._status_lock:
            self._status_cache = result
            self._status_cache_at = monotonic()
        return dict(result)

    def recognize(self, image_path: Path) -> dict[str, object]:
        status = self.status()
        if not status["ready"]:
            if not status["checks"].get("inferenceDependencies", False):
                raise FormulaRecognitionNotReady(
                    "公式识别运行时缺少 torch 等推理依赖，请安装到 runtime/.venv"
                )
            raise FormulaRecognitionNotReady("UniMERNet 识别环境尚未完成配置")

        root = Path(self.app.config["FORMULA_RECOGNITION_ROOT"])
        command = [
            str(Path(self.app.config["FORMULA_RECOGNITION_PYTHON"])),
            str(root / "recognize.py"),
            str(image_path.resolve()),
            "--device",
            str(self.app.config["FORMULA_RECOGNITION_DEVICE"]),
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Do not let a user's global Python packages override the vendored
        # runtime; mixed Torch/NumPy installations can make torchvision fail
        # before the model is even loaded.
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUTF8"] = "1"
        cpu_threads = str(self.app.config["FORMULA_RECOGNITION_CPU_THREADS"])
        env.update(
            {
                "OMP_NUM_THREADS": cpu_threads,
                "MKL_NUM_THREADS": cpu_threads,
                "OPENBLAS_NUM_THREADS": cpu_threads,
                "NUMEXPR_NUM_THREADS": cpu_threads,
                "TOKENIZERS_PARALLELISM": "false",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
        )
        started_at = perf_counter()
        try:
            with self._inference_lock:
                result = subprocess.run(
                    command,
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=float(self.app.config["FORMULA_RECOGNITION_TIMEOUT_SECONDS"]),
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise FormulaRecognitionFailed("公式识别超时，请稍后重试") from exc
        except OSError as exc:
            raise FormulaRecognitionFailed("无法启动公式识别运行时", detail=str(exc)) from exc

        if result.returncode != 0:
            detail = "\n".join(line for line in result.stderr.splitlines() if line.strip())[-4000:]
            if result.returncode < 0:
                detail = f"运行时被信号终止（returncode={result.returncode}）。\n{detail}".strip()
            elif not detail:
                detail = f"运行时退出码：{result.returncode}。输出：{result.stdout[-1000:]}"
            raise FormulaRecognitionFailed("公式识别执行失败", detail=detail)
        latex = result.stdout.strip()
        if not latex:
            raise FormulaRecognitionFailed("识别服务未返回 LaTeX 结果", detail=result.stderr[-4000:])
        return {
            "latex": latex,
            "device": status["device"],
            "durationMs": round((perf_counter() - started_at) * 1000),
        }
