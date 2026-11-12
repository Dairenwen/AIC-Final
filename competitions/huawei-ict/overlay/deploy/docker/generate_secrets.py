#!/usr/bin/env python3
"""Create deploy/docker/.env with freshly generated secrets.

The handed-over deploy/huawei/.env carried the previous deployment's
SECRET_KEY and MODEL_CONFIG_ENCRYPTION_KEY. Those are not reused here: new
random values are generated so nothing from the old environment can decrypt
anything in this one, and the three model API keys are left empty for the
operator to fill in.

Changing MODEL_CONFIG_ENCRYPTION_KEY can make previously stored credentials
unreadable. Existing database records are never cleared by this script.

Values are only generated for keys that are still empty, so re-running this
never overwrites a key the operator has filled in.

  generate_secrets.py [--env-file PATH] [--force]
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import secrets
import stat
import sys

HERE = Path(__file__).resolve().parent

# key -> (generator, comment).  A generator of None means "leave empty".
GENERATED = {
    # Flask session signing.
    "SECRET_KEY": lambda: secrets.token_urlsafe(48),
    # AES-GCM master key for stored model credentials: URL-safe Base64 that
    # decodes to exactly 32 bytes (see app/services/model_credentials.py).
    "MODEL_CONFIG_ENCRYPTION_KEY": lambda: base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode("ascii"),
    # Local database password; the port is only published on 127.0.0.1.
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=HERE / ".env")
    parser.add_argument(
        "--force", action="store_true",
        help="重新生成上述三个密钥，即使文件里已有值（会使已存的模型密钥无法解密）",
    )
    args = parser.parse_args()

    template = HERE / ".env.example"
    if not template.is_file():
        print(f"缺少模板 {template}", file=sys.stderr)
        return 1

    if args.env_file.is_file():
        lines = args.env_file.read_text(encoding="utf-8").splitlines()
        created = False
    else:
        lines = template.read_text(encoding="utf-8").splitlines()
        created = True

    filled: list[str] = []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        seen.add(key)
        if key in GENERATED and (args.force or not value.strip()):
            out.append(f"{key}={GENERATED[key]()}")
            filled.append(key)
        else:
            out.append(line)

    for key, generate in GENERATED.items():
        if key not in seen:
            out.append(f"{key}={generate()}")
            filled.append(key)

    args.env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    # Secrets file: owner read/write only.
    os.chmod(args.env_file, stat.S_IRUSR | stat.S_IWUSR)

    print(f"{'已创建' if created else '已更新'} {args.env_file}（权限 0600）")
    if filled:
        print("已生成：" + "、".join(filled))
    else:
        print("三个系统密钥都已存在，未改动。需要轮换请加 --force。")
    print("模型 API Key 留空：由每位用户在「个人中心 / 模型库」自行填写，")
    print("或在该文件里设置 USER_API_MODEL_API_KEY / TRANSLATION_API_KEY / CHUNK_QA_MODEL_API_KEY。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
