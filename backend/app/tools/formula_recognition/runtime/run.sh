#!/usr/bin/env bash
# First run: create a local venv, install dependencies, download the model,
# then recognize the supplied formula image.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: ./run.sh /path/to/formula-image.png [auto|mps|cuda|cpu]" >&2
  exit 64
fi

TOOL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$TOOL_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
MODEL_DIR="$TOOL_DIR/unimernet/models/unimernet_base"
IMAGE="$1"
DEVICE="${2:-auto}"
# Keep build paths short: the checkout path contains Chinese characters and is
# already close to Windows MAX_PATH for legacy source distributions.
DRIVE_ROOT="${TOOL_DIR%%/*}/"
TEMP_DIR="${DRIVE_ROOT}zhiyan-formula-tmp"
PIP_CACHE_DIR="${DRIVE_ROOT}zhiyan-formula-pip-cache"
mkdir -p "$TEMP_DIR" "$PIP_CACHE_DIR"
export TMPDIR="$TEMP_DIR"
export TEMP="$TEMP_DIR"
export TMP="$TEMP_DIR"
export PIP_CACHE_DIR="$PIP_CACHE_DIR"

if [[ ! -x "$PYTHON" ]]; then
  python3 -m venv "$VENV_DIR"
fi

if ! "$PYTHON" -c 'import torch, transformers, unimernet' >/dev/null 2>&1; then
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install --disable-pip-version-check --no-cache-dir "setuptools<81" wheel
  "$PYTHON" -m pip install --disable-pip-version-check --no-cache-dir --prefer-binary --no-build-isolation -r "$TOOL_DIR/requirements-inference.txt"
  "$PYTHON" -m pip install --editable "$TOOL_DIR/unimernet"
fi

if [[ ! -f "$MODEL_DIR/pytorch_model.pth" ]]; then
  "$PYTHON" "$TOOL_DIR/download_model.py" "$MODEL_DIR"
fi

exec "$PYTHON" "$TOOL_DIR/recognize.py" "$IMAGE" --device "$DEVICE"
