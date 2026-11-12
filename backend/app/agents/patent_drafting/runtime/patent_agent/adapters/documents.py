from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from patent_agent.errors import ParseError


TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".py", ".go", ".java", ".js", ".ts", ".tsx", ".rs", ".c", ".h", ".cpp", ".hpp"}


class DocumentParser:
    def __init__(self, vendor_root: Path):
        self.vendor_root = vendor_root

    def parse(self, source: Path, output_dir: Path) -> str:
        suffix = source.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            try:
                return source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return source.read_text(encoding="utf-8", errors="replace")
        if suffix in {".docx", ".pptx", ".ppsx"}:
            script = "docx_to_md.py" if suffix == ".docx" else "pptx_to_md.py"
            out = output_dir / f"{source.stem}.md"
            try:
                proc = subprocess.run(
                    [sys.executable, str(self.vendor_root / "tools" / script), "-i", str(source), "-o", str(out)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
            except subprocess.TimeoutExpired:
                raise ParseError(f"{suffix} parser timed out for {source.name}") from None
            except OSError as exc:
                raise ParseError(f"{suffix} parser could not start for {source.name}: {type(exc).__name__}") from None
            if proc.returncode != 0 or not out.is_file():
                raise ParseError(f"{suffix} parser failed for {source.name}; details suppressed")
            return out.read_text(encoding="utf-8")
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader

                pages = [page.extract_text() or "" for page in PdfReader(str(source)).pages]
                text = "\n\n".join(pages).strip()
                if not text:
                    raise ParseError(f"PDF contains no extractable text: {source.name}")
                return text
            except ParseError:
                raise
            except Exception as exc:
                raise ParseError(f"text PDF parser failed for {source.name}: {type(exc).__name__}") from None
        raise ParseError(f"unsupported input format: {source.suffix}")
