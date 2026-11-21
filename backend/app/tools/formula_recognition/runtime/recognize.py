#!/usr/bin/env python3
"""Recognize one mathematical-formula image and print its LaTeX code.

This is a small non-interactive wrapper around the vendored UniMERNet model.
It intentionally does not modify the upstream source tree.
"""

from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import sys

import torch
from PIL import Image


TOOL_DIR = Path(__file__).resolve().parent
UPSTREAM_DIR = TOOL_DIR / "unimernet"


def select_device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    return torch.device(requested)


def build_model_low_memory(task, config, checkpoint: Path):
    """Build UniMERNet without holding two full copies of its weights in RAM.

    The upstream path allocates every parameter, initialises it randomly and
    then reads the 1.3 GB checkpoint into a second copy, which needs well over
    3 GB and gets the process killed on small servers.  Here the parameters
    are created as shape-only placeholders and then bound directly to the
    checkpoint, which is memory-mapped: the weights stay in the page cache
    and can be dropped by the kernel instead of being swapped.
    """
    register = torch.nn.Module.register_parameter

    def register_placeholder(module, name, param):
        register(module, name, param)
        if param is not None:
            kept = module._parameters[name]
            module._parameters[name] = torch.nn.Parameter(kept.to("meta"), requires_grad=kept.requires_grad)

    config.config.model.load_pretrained = False
    torch.nn.Module.register_parameter = register_placeholder
    try:
        model = task.build_model(config)
    finally:
        torch.nn.Module.register_parameter = register
    state = torch.load(checkpoint, map_location="cpu", mmap=True, weights_only=False)
    state = state.get("model", state)
    result = model.load_state_dict(state, strict=False, assign=True)
    unresolved = [name for name, value in model.named_parameters() if value.is_meta]
    if unresolved:
        raise RuntimeError(f"Checkpoint is missing {len(unresolved)} weights, e.g. {unresolved[:3]}")
    del result
    return model


def recognize(image_path: Path, device_name: str) -> str:
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # UniMERNet's supplied configuration uses paths relative to its repository.
    os.chdir(UPSTREAM_DIR)
    from unimernet.common.config import Config
    import unimernet.tasks as tasks
    from unimernet.processors import load_processor

    config_path = UPSTREAM_DIR / "configs" / "demo.yaml"
    args = argparse.Namespace(cfg_path=str(config_path), options=None)
    config = Config(args)
    # The current Hugging Face checkpoint is named ``pytorch_model.pth`` while
    # the upstream demo YAML still refers to the old ``unimernet_base.pth``.
    # Keep that compatibility fix in this wrapper instead of changing vendored
    # source files.
    checkpoint = UPSTREAM_DIR / "models" / "unimernet_base" / "pytorch_model.pth"
    config.config.model.pretrained = str(checkpoint)
    # Some upstream constructors write progress messages to stdout.  Redirect
    # those diagnostics so callers can safely consume this tool's stdout as
    # one LaTeX string.
    with contextlib.redirect_stdout(sys.stderr):
        task = tasks.setup_task(config)
        device = select_device(device_name)
        model = build_model_low_memory(task, config, checkpoint).to(device).eval()
        processor = load_processor(
            "formula_image_eval",
            config.config.datasets.formula_rec_eval.vis_processor.eval,
        )

    with Image.open(image_path) as original:
        image = original.convert("RGB")
        batch = processor(image).unsqueeze(0).to(device)
    with torch.inference_mode(), contextlib.redirect_stdout(sys.stderr):
        return model.generate({"image": batch})["pred_str"][0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a single formula image to LaTeX with UniMERNet."
    )
    parser.add_argument("image", type=Path, help="Path to a PNG, JPG, or other PIL-readable image")
    parser.add_argument(
        "--device",
        choices=("auto", "mps", "cuda", "cpu"),
        default="auto",
        help="Inference device (default: auto; Apple Silicon uses MPS when available)",
    )
    args = parser.parse_args()
    print(recognize(args.image.resolve(), args.device))


if __name__ == "__main__":
    main()
