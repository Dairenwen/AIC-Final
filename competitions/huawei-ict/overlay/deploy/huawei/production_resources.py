#!/usr/bin/env python3
"""Report resource estimates without claiming they guarantee successful deployment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

GIB = 1024 ** 3


def describe(machine: str, available_memory: int | None, docker_free: int | None) -> list[str]:
    messages = [f"服务器 CPU 架构：{machine}"]
    if machine.lower() not in {"x86_64", "amd64"}:
        messages.append("提示：本交付的完整镜像目前仅经过 x86_64 构建验证；当前架构仍需实测。")
    if available_memory is None:
        messages.append("无法读取当前可用内存，请在服务器上人工检查。")
    else:
        messages.append(f"当前可用内存：约 {available_memory / GIB:.1f} GiB。")
        if available_memory < 8 * GIB:
            messages.append("提示：可用内存低于 8 GiB；搜索服务及本地 CPU 模型可能无法稳定运行。")
    if docker_free is None:
        messages.append("无法读取 Docker 存储目录的剩余空间；若 Docker 在远端，请在服务器上人工检查。")
    else:
        messages.append(f"Docker 存储所在文件系统可用空间：约 {docker_free / GIB:.1f} GiB。")
        if docker_free < 20 * GIB:
            messages.append("提示：可用空间低于 20 GiB；首次构建及运行可能空间不足（后端镜像约 8.7 GiB，构建还需要中间层和数据空间）。")
    messages.append("以上数值为当前可用资源和参考提示，实际需求取决于容器、数据及并发；不代表功能验收。")
    return messages


def available_memory() -> int | None:
    try:
        for line in Path('/proc/meminfo').read_text(encoding='ascii').splitlines():
            fields = line.split()
            if fields and fields[0] == 'MemAvailable:' and len(fields) == 3 and fields[2] == 'kB':
                return int(fields[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-root', required=True)
    args = parser.parse_args()
    try:
        docker_free = shutil.disk_usage(args.docker_root).free
    except OSError:
        docker_free = None
    for line in describe(os.uname().machine, available_memory(), docker_free):
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
