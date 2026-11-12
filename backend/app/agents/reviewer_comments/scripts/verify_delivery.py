"""交付自测脚本：离线 / 联机验收。

用法（在 langgraph-agent 包根执行，或本脚本自行 chdir）::

    python scripts/verify_delivery.py              # 默认 --offline
    python scripts/verify_delivery.py --offline
    python scripts/verify_delivery.py --live

退出码约定
----------
- 0：全部通过
- 1：检查或命令失败
- 2：--live 因缺少配置而跳过（未跑 live）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 包根与路径
# ---------------------------------------------------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _configure_stdio() -> None:
    """尽量让 stdout/stderr 用 UTF-8，避免 Windows GBK 打印中文/替换符崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


REQUIRED_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "README.md",
    "DELIVERY.md",
    ".env.example",
    "alembic.ini",
    "migrations/env.py",
    "scripts/init_db.py",
    "scripts/_bootstrap.py",
    "main.py",
)

LIVE_ENV_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
)

# 子进程超时（秒）：offline demo / pytest 通常很快；live 可能较长
_OFFLINE_TIMEOUT = 300
_LIVE_TIMEOUT = 600
_CMD_TAIL_CHARS = 2000


def ensure_package_root() -> Path:
    """将 cwd 切到包根，并把包根/src 注入 path，返回包根。"""
    root = PACKAGE_ROOT
    os.chdir(root)
    src = root / "src"
    for path in (str(root), str(src)):
        if path not in sys.path:
            sys.path.insert(0, path)
    return root


def check_required_files(root: Path | None = None) -> list[str]:
    """检查关键交付文件是否存在；返回缺失路径列表（相对包根）。

    同时要求 ``migrations/versions/`` 下至少有一个 ``*.py`` 迁移文件
    （排除 ``__init__.py``）。
    """
    base = root if root is not None else PACKAGE_ROOT
    missing: list[str] = []
    for rel in REQUIRED_FILES:
        if not (base / rel).is_file():
            missing.append(rel)

    versions_dir = base / "migrations" / "versions"
    if not versions_dir.is_dir():
        missing.append("migrations/versions/")
    else:
        version_files = [
            p
            for p in versions_dir.glob("*.py")
            if p.name != "__init__.py" and p.is_file()
        ]
        if not version_files:
            missing.append("migrations/versions/*.py（至少一个 version）")
    return missing


def _safe_print(text: str) -> None:
    """Windows GBK 控制台下避免因替换字符/非 BMP 导致崩溃。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        rendered = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(rendered)


def _print_step(title: str) -> None:
    _safe_print(f"\n=== {title} ===")


def _print_fail(step: str, detail: str = "") -> None:
    _safe_print(f"[失败] {step}")
    if detail:
        _safe_print(detail)


def _print_ok(step: str) -> None:
    _safe_print(f"[通过] {step}")


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """子进程环境：继承当前环境，并强制 UTF-8 IO（Windows 友好）。"""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def _run_subprocess(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """用当前解释器跑子进程；Windows 友好（list 参数，无 shell）。"""
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=_subprocess_env(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _tail(text: str, limit: int = _CMD_TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def check_import() -> tuple[bool, str]:
    """验证公共 API 可导入。"""
    try:
        from langgraph_agent import AgentResult, ReviewAgent  # noqa: F401
    except Exception as error:  # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"
    return True, "from langgraph_agent import ReviewAgent, AgentResult"


def check_demo_offline(root: Path) -> tuple[bool, str]:
    """子进程跑 ``python main.py demo-offline``。"""
    try:
        proc = _run_subprocess(
            [sys.executable, "main.py", "demo-offline"],
            cwd=root,
            timeout=_OFFLINE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（>{_OFFLINE_TIMEOUT}s）"
    if proc.returncode != 0:
        detail = (
            f"退出码={proc.returncode}\n"
            f"--- stdout 尾部 ---\n{_tail(proc.stdout)}\n"
            f"--- stderr 尾部 ---\n{_tail(proc.stderr)}"
        )
        return False, detail
    return True, "python main.py demo-offline"


def check_unit_tests(root: Path) -> tuple[bool, str]:
    """子进程跑非 integration 的 pytest。"""
    try:
        proc = _run_subprocess(
            [sys.executable, "-m", "pytest", "-q", "-m", "not integration"],
            cwd=root,
            timeout=_OFFLINE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（>{_OFFLINE_TIMEOUT}s）"
    if proc.returncode != 0:
        detail = (
            f"退出码={proc.returncode}\n"
            f"--- stdout 尾部 ---\n{_tail(proc.stdout)}\n"
            f"--- stderr 尾部 ---\n{_tail(proc.stderr)}"
        )
        return False, detail
    summary = (proc.stdout or "").strip().splitlines()
    last = summary[-1] if summary else "pytest 通过"
    return True, last


def _load_dotenv_if_present(root: Path) -> None:
    """若存在 .env 则加载（不覆盖已有环境变量）。"""
    env_path = root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # 无 python-dotenv 时手工解析简单 KEY=VALUE
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value
        return
    load_dotenv(env_path, override=False)


def check_live_env(root: Path) -> tuple[list[str], dict[str, str]]:
    """检查 live 所需环境变量；返回 (缺失键列表, 当前值字典)。"""
    _load_dotenv_if_present(root)
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in LIVE_ENV_KEYS:
        val = (os.environ.get(key) or "").strip()
        values[key] = val
        if not val:
            missing.append(key)
    return missing, values


def run_live(root: Path) -> int:
    """联机验收：init_db + demo-task-init --live --auto-approve。

    Returns:
        0 成功；1 命令失败；2 配置缺失跳过。
    """
    _print_step("联机配置检查")
    missing, _ = check_live_env(root)
    if missing:
        joined = "、".join(missing)
        _safe_print(f"跳过 live：缺少 {joined}")
        _safe_print("（退出码 2 = 未跑 live，非命令失败）")
        return 2

    _print_ok("DATABASE_URL / LLM_BASE_URL / LLM_API_KEY 已配置")

    _print_step("python scripts/init_db.py")
    try:
        proc = _run_subprocess(
            [sys.executable, "scripts/init_db.py"],
            cwd=root,
            timeout=_LIVE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _print_fail("init_db.py", f"超时（>{_LIVE_TIMEOUT}s）")
        return 1
    if proc.returncode != 0:
        _print_fail(
            "init_db.py",
            f"退出码={proc.returncode}\n--- stderr 尾部 ---\n{_tail(proc.stderr)}\n"
            f"--- stdout 尾部 ---\n{_tail(proc.stdout)}",
        )
        return 1
    _print_ok("init_db.py")
    if proc.stdout:
        _safe_print(_tail(proc.stdout, 800))

    _print_step("python main.py demo-task-init --live --auto-approve")
    try:
        proc = _run_subprocess(
            [
                sys.executable,
                "main.py",
                "demo-task-init",
                "--live",
                "--auto-approve",
            ],
            cwd=root,
            timeout=_LIVE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _print_fail("demo-task-init --live", f"超时（>{_LIVE_TIMEOUT}s）")
        return 1
    if proc.returncode != 0:
        _print_fail(
            "demo-task-init --live --auto-approve",
            f"退出码={proc.returncode}\n--- stderr 尾部 ---\n{_tail(proc.stderr)}\n"
            f"--- stdout 尾部 ---\n{_tail(proc.stdout)}",
        )
        return 1
    _print_ok("demo-task-init --live --auto-approve")
    if proc.stdout:
        _safe_print(_tail(proc.stdout, 800))
    return 0


def run_offline(root: Path) -> int:
    """离线验收：文件、import、demo-offline、pytest。失败返回 1。"""
    failed = False

    _print_step("关键文件存在")
    missing = check_required_files(root)
    if missing:
        failed = True
        _print_fail("关键文件", "缺失：\n  - " + "\n  - ".join(missing))
    else:
        _print_ok(f"关键文件齐全（{len(REQUIRED_FILES)} 项 + migrations/versions）")

    _print_step("公共 API import")
    ok, detail = check_import()
    if not ok:
        failed = True
        _print_fail("import", detail)
    else:
        _print_ok(detail)

    _print_step("python main.py demo-offline")
    ok, detail = check_demo_offline(root)
    if not ok:
        failed = True
        _print_fail("demo-offline", detail)
    else:
        _print_ok(detail)

    _print_step('python -m pytest -q -m "not integration"')
    ok, detail = check_unit_tests(root)
    if not ok:
        failed = True
        _print_fail("pytest (not integration)", detail)
    else:
        _print_ok(detail)

    if failed:
        _safe_print("\n离线验收失败。请按上方失败步骤排查。")
        return 1
    _safe_print("\n离线验收全部通过。")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="langgraph-agent 交付自测（离线 / 联机）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--offline",
        action="store_true",
        help="离线验收（默认）：文件 / import / demo-offline / 单元测试",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="联机验收：需 DATABASE_URL / LLM_*；缺配置 exit 2",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = parse_args(argv)
    root = ensure_package_root()
    _safe_print(f"包根：{root}")
    _safe_print(f"解释器：{sys.executable}")

    if args.live:
        return run_live(root)
    # 默认 offline（含显式 --offline）
    return run_offline(root)


if __name__ == "__main__":
    raise SystemExit(main())
