"""交付脚本轻量冒烟测试（不连外网、不要求 DATABASE_URL）。

完整 ``verify_delivery --offline``（含 demo-offline + 全量 pytest）留给
人工 / 交付命令，避免测试套娃过久。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"


def _load_script_module(name: str, filename: str):
    """按文件路径加载 scripts/ 下模块（不依赖 package 安装名）。"""
    path = SCRIPTS_DIR / filename
    assert path.is_file(), f"缺少脚本：{path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 避免与其它测试冲突：用唯一模块名
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify_mod():
    return _load_script_module(
        "_delivery_verify_delivery",
        "verify_delivery.py",
    )


@pytest.fixture(scope="module")
def pack_mod():
    return _load_script_module(
        "_delivery_pack_check",
        "pack_check.py",
    )


# ---------------------------------------------------------------------------
# pack_check
# ---------------------------------------------------------------------------


def test_pack_check_importable(pack_mod) -> None:
    assert hasattr(pack_mod, "scan_package")
    assert hasattr(pack_mod, "main")
    assert callable(pack_mod.scan_package)


def test_pack_check_scan_runs(pack_mod) -> None:
    """对真实包根扫描应能完成；无 .env 时不应有 .env 高风险。"""
    result = pack_mod.scan_package(PACKAGE_ROOT)
    assert result is not None
    env_highs = [f for f in result.high if f.path == ".env" or f.path.endswith("/.env")]
    # 本仓库交付态不应提交 .env；若本地有 .env 则允许高风险（不强制 fail 测试）
    if not (PACKAGE_ROOT / ".env").is_file():
        assert env_highs == []


def test_pack_check_detects_env_in_tmp(pack_mod, tmp_path: Path) -> None:
    """临时目录放置 .env 应判为高风险。"""
    (tmp_path / ".env").write_text("LLM_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "x.txt").write_text("tmp", encoding="utf-8")
    result = pack_mod.scan_package(tmp_path)
    assert result.has_high
    assert any(f.path == ".env" for f in result.high)
    assert any(".tmp" in f.path for f in result.medium)


def test_pack_check_main_exit_code_clean(pack_mod, tmp_path: Path) -> None:
    """干净目录 main() 应 exit 0。"""
    code = pack_mod.main(["--root", str(tmp_path)])
    assert code == 0


def test_pack_check_main_exit_code_with_env(pack_mod, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("x=1\n", encoding="utf-8")
    code = pack_mod.main(["--root", str(tmp_path)])
    assert code == 1


# ---------------------------------------------------------------------------
# verify_delivery：文件清单（可单测）
# ---------------------------------------------------------------------------


def test_verify_required_files_constant(verify_mod) -> None:
    required = verify_mod.REQUIRED_FILES
    assert "pyproject.toml" in required
    assert "main.py" in required
    assert "scripts/_bootstrap.py" in required
    assert "scripts/init_db.py" in required
    assert "migrations/env.py" in required


def test_check_required_files_on_real_package(verify_mod) -> None:
    missing = verify_mod.check_required_files(PACKAGE_ROOT)
    assert missing == [], f"交付包缺失文件：{missing}"


def test_check_required_files_detects_missing(verify_mod, tmp_path: Path) -> None:
    missing = verify_mod.check_required_files(tmp_path)
    assert "pyproject.toml" in missing
    assert "main.py" in missing
    assert any("migrations" in m for m in missing)


def test_check_required_files_needs_version_py(verify_mod, tmp_path: Path) -> None:
    """有 versions 目录但无迁移 py 时也应报告缺失。"""
    for rel in verify_mod.REQUIRED_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# stub\n", encoding="utf-8")
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / "__init__.py").write_text("", encoding="utf-8")
    missing = verify_mod.check_required_files(tmp_path)
    assert any("versions" in m for m in missing)


def test_verify_parse_args_default_offline(verify_mod) -> None:
    args = verify_mod.parse_args([])
    assert args.live is False
    # 未传 --offline 时也走 offline 主路径（main 内默认）
    args2 = verify_mod.parse_args(["--offline"])
    assert args2.offline is True
    assert args2.live is False
    args3 = verify_mod.parse_args(["--live"])
    assert args3.live is True


def test_live_env_keys_documented(verify_mod) -> None:
    keys = set(verify_mod.LIVE_ENV_KEYS)
    assert keys == {"DATABASE_URL", "LLM_BASE_URL", "LLM_API_KEY"}
