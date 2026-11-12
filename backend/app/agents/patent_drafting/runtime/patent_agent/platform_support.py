from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


def interpreter_kind() -> str:
    """Describe whether the running interpreter is native to the host platform."""
    platform_tag = sysconfig.get_platform().casefold()
    if os.name == "nt":
        return "native_windows" if platform_tag.startswith("win-") else "windows_non_native"
    return "posix"


def virtualenv_python(venv_dir: Path) -> Path:
    """Return an existing venv interpreter across Windows, POSIX, and MSYS layouts."""
    candidates = (
        venv_dir / "Scripts" / "python.exe",
        venv_dir / "bin" / "python",
        venv_dir / "bin" / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    preferred = candidates[0] if os.name == "nt" else candidates[1]
    raise FileNotFoundError(f"virtual environment interpreter not found; expected {preferred}")


def find_npm_executable() -> str | None:
    names = ("npm.cmd", "npm.exe", "npm") if os.name == "nt" else ("npm",)
    return next((resolved for name in names if (resolved := shutil.which(name))), None)


def find_mermaid_cli(tools_dir: Path) -> Path | None:
    local_bin = tools_dir / "node_modules" / ".bin"
    names = ("mmdc.cmd", "mmdc.exe", "mmdc", "mmdc.ps1") if os.name == "nt" else ("mmdc", "mmdc.cmd")
    return next((candidate for name in names if (candidate := local_bin / name).is_file()), None)


def find_puppeteer_browser(tools_dir: Path) -> Path | None:
    cache = tools_dir / ".puppeteer-cache"
    if not cache.is_dir():
        return None
    executable_names = (
        {"chrome.exe", "chrome-headless-shell.exe"}
        if os.name == "nt"
        else {"chrome", "chrome-headless-shell", "Google Chrome for Testing"}
    )
    return next(
        (
            candidate
            for candidate in cache.rglob("*")
            if candidate.is_file() and candidate.name in executable_names
        ),
        None,
    )


def _normalized_font_labels(label: str) -> set[str]:
    cleaned = re.sub(r"\s+\((?:TrueType|OpenType|All Res)\)\s*$", "", label, flags=re.IGNORECASE).strip()
    if not cleaned:
        return set()
    labels = {cleaned.casefold()}
    labels.update(part.strip().casefold() for part in cleaned.split(" & ") if part.strip())
    style_suffix = re.compile(
        r"\s+(?:regular|bold|italic|bold italic|light|medium|semibold|demibold|black|thin)$",
        flags=re.IGNORECASE,
    )
    labels.update(style_suffix.sub("", item).strip() for item in tuple(labels))
    return labels


def _windows_font_families() -> set[str]:
    if os.name != "nt":
        return set()
    try:
        import winreg
    except ImportError:
        return set()

    families: set[str] = set()
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    for hive, key_name in locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                index = 0
                while True:
                    try:
                        value_name, _value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    families.update(_normalized_font_labels(value_name))
                    index += 1
        except OSError:
            continue
    return families


def _fontconfig_families() -> set[str]:
    fc_list = shutil.which("fc-list")
    if not fc_list:
        return set()
    try:
        proc = subprocess.run(
            [fc_list, ":", "family"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return {
        part.strip().casefold()
        for line in proc.stdout.splitlines()
        for part in line.split(",")
        if part.strip()
    }


def installed_font_families() -> tuple[set[str], str]:
    if os.name == "nt":
        return _windows_font_families(), "windows_registry"
    return _fontconfig_families(), "fontconfig"


def font_available(font_name: str) -> tuple[bool, str]:
    families, detector = installed_font_families()
    return font_name.strip().casefold() in families, detector
