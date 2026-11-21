#!/usr/bin/env python3
"""Download the five UniMERNet model files directly, without a cache."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests


REPOSITORY = "https://huggingface.co/wanderkid/unimernet_base/resolve/main"
FILES = (
    "config.json",
    "preprocessor_config.json",
    "pytorch_model.pth",
    "tokenizer.json",
    "tokenizer_config.json",
)
ATTEMPTS = 4


def download(url: str, destination: Path) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with requests.get(url, stream=True, timeout=(20, 300)) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if destination.stat().st_size == 0:
                raise RuntimeError("received an empty file")
            return
        except Exception as error:
            if attempt == ATTEMPTS:
                raise RuntimeError(f"Failed to download {destination.name}: {error}") from error
            delay = attempt * 3
            print(
                f"Download attempt {attempt}/{ATTEMPTS} for {destination.name} failed; retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: download_model.py MODEL_DIRECTORY")
    model_dir = Path(sys.argv[1]).resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in FILES:
        destination = model_dir / filename
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        print(f"Downloading {filename}...", file=sys.stderr)
        download(f"{REPOSITORY}/{filename}", destination)


if __name__ == "__main__":
    main()
