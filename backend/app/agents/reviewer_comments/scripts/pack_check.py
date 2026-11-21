"""打包风险检查：只报告、不删除。

扫描 langgraph-agent/（可跳过 .venv）：
  高风险：存在 .env；存在疑似密钥文件
  中风险：.tmp/ 非空；__pycache__；.pytest_cache；*.egg-info

有高风险 exit 1，否则 exit 0（中风险仅提示）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# 扫描时跳过的目录名
_SKIP_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        ".git",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "htmlcov",
        "dist",
        "build",
    }
)

# 文件名完全匹配 → 高风险（密钥/凭证）
_SECRET_EXACT_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.prod",
        ".env.staging",
        "credentials.json",
        "service_account.json",
        "secrets.json",
        "secret.json",
        "id_rsa",
        "id_ed25519",
        "private_key.pem",
        "private.pem",
    }
)

# 后缀 → 高风险
_SECRET_SUFFIXES = (
    ".pem",
    ".p12",
    ".pfx",
    ".key",
)

# 文件名片段（小写）→ 高风险；排除 .env.example 等
_SECRET_NAME_FRAGMENTS = (
    "credentials",
    "secret",
    "private_key",
    "api_key",
    "apikey",
)

_SAFE_ENV_NAMES = frozenset(
    {
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.dist",
    }
)


@dataclass
class Finding:
    level: str  # high | medium
    path: str  # 相对包根
    reason: str


@dataclass
class ScanResult:
    high: list[Finding] = field(default_factory=list)
    medium: list[Finding] = field(default_factory=list)

    @property
    def has_high(self) -> bool:
        return bool(self.high)


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _is_secret_file(path: Path) -> str | None:
    """若为疑似密钥文件，返回原因；否则 None。"""
    name = path.name
    lower = name.lower()

    if lower in _SAFE_ENV_NAMES:
        return None

    if name in _SECRET_EXACT_NAMES or lower in {n.lower() for n in _SECRET_EXACT_NAMES}:
        return f"疑似密钥/凭证文件：{name}"

    # .env.* 变体（已排除 example/sample）
    if lower.startswith(".env.") or lower == ".env":
        return f"环境变量密钥文件：{name}"

    for suffix in _SECRET_SUFFIXES:
        if lower.endswith(suffix):
            # 常见非密钥：公钥 .pub 已不在列表；.pem 一律高风险
            return f"疑似私钥/证书文件（{suffix}）：{name}"

    stem_lower = path.stem.lower()
    for frag in _SECRET_NAME_FRAGMENTS:
        if frag in stem_lower or frag in lower:
            # 避免误报：如 test_secret_helper.py 源码 — 仅对无扩展或特定扩展
            if path.suffix.lower() in {".json", ".yml", ".yaml", ".toml", ".txt", ".env", ""}:
                return f"文件名含敏感片段「{frag}」：{name}"
    return None


def _dir_nonempty(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False


def scan_package(root: Path | None = None) -> ScanResult:
    """扫描包根，返回高/中风险清单。"""
    base = (root if root is not None else PACKAGE_ROOT).resolve()
    result = ScanResult()
    seen_pycache: set[str] = set()
    seen_egg: set[str] = set()
    has_pytest_cache = False

    # 1) 根级 .env
    env_file = base / ".env"
    if env_file.is_file():
        result.high.append(
            Finding("high", ".env", "存在真实 .env（含密钥风险，禁止打包提交）")
        )

    # 2) .tmp/ 非空
    tmp_dir = base / ".tmp"
    if _dir_nonempty(tmp_dir):
        result.medium.append(
            Finding("medium", ".tmp/", ".tmp/ 目录非空（导出/临时产物，建议不打包）")
        )

    # 3) 根级缓存目录
    pytest_cache = base / ".pytest_cache"
    if pytest_cache.exists():
        has_pytest_cache = True
        result.medium.append(
            Finding("medium", ".pytest_cache/", "存在 pytest 缓存目录")
        )

    # 4) 遍历
    for dirpath, dirnames, filenames in os_walk_skip(base):
        current = Path(dirpath)

        # __pycache__
        if current.name == "__pycache__":
            rel = _rel(base, current)
            # 只记到上一级，避免刷屏：按第一层 __pycache__ 路径
            top = rel.split("/__pycache__")[0] + "/__pycache__"
            if top not in seen_pycache:
                seen_pycache.add(top)
                result.medium.append(
                    Finding("medium", top, "存在 __pycache__ 字节码缓存")
                )
            dirnames.clear()  # 不再深入
            continue

        if current.name == ".pytest_cache" and not has_pytest_cache:
            has_pytest_cache = True
            result.medium.append(
                Finding(
                    "medium",
                    _rel(base, current) + "/",
                    "存在 pytest 缓存目录",
                )
            )
            dirnames.clear()
            continue

        if current.name.endswith(".egg-info") or current.name.endswith(".egg"):
            rel = _rel(base, current) + "/"
            if rel not in seen_egg:
                seen_egg.add(rel)
                result.medium.append(
                    Finding("medium", rel, "存在 egg-info / 本地构建元数据")
                )
            dirnames.clear()
            continue

        for filename in filenames:
            fpath = current / filename
            # 根 .env 已单独报过
            if fpath.resolve() == env_file.resolve():
                continue
            reason = _is_secret_file(fpath)
            if reason:
                result.high.append(Finding("high", _rel(base, fpath), reason))

            # 散落的 .egg-info 当文件极少见；目录已处理
            if filename.endswith(".egg-info"):
                rel = _rel(base, fpath)
                if rel not in seen_egg:
                    seen_egg.add(rel)
                    result.medium.append(
                        Finding("medium", rel, "存在 egg-info 元数据")
                    )

    return result


def os_walk_skip(base: Path):
    """os.walk 封装：就地剔除跳过目录。"""
    import os

    for dirpath, dirnames, filenames in os.walk(base):
        # 就地修改，阻止下降
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".venv")
        )
        yield dirpath, dirnames, filenames


def format_report(result: ScanResult) -> str:
    lines: list[str] = []
    lines.append("打包风险检查报告")
    lines.append("=" * 40)
    if result.high:
        lines.append(f"\n【高风险】{len(result.high)} 项（存在则 exit 1）")
        for item in result.high:
            lines.append(f"  - {item.path}: {item.reason}")
    else:
        lines.append("\n【高风险】无")

    if result.medium:
        lines.append(f"\n【中风险】{len(result.medium)} 项（仅提示，不导致失败）")
        for item in result.medium:
            lines.append(f"  - {item.path}: {item.reason}")
    else:
        lines.append("\n【中风险】无")

    if result.has_high:
        lines.append("\n结论：存在高风险项，请清理后再打包。")
    else:
        lines.append("\n结论：无高风险项，可打包（中风险请自行决定是否清理）。")
    return "\n".join(lines)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="langgraph-agent 打包风险检查（只报告）")
    parser.add_argument(
        "--root",
        type=str,
        default="",
        help="包根路径（默认：本脚本所在包根）",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else PACKAGE_ROOT
    if not root.is_dir():
        _safe_print(f"[失败] 包根不存在：{root}")
        return 1

    _safe_print(f"扫描目录：{root}")
    result = scan_package(root)
    _safe_print(format_report(result))
    return 1 if result.has_high else 0


if __name__ == "__main__":
    raise SystemExit(main())
