"""文件上传与解析服务"""

import os
import uuid
from typing import Dict, Optional
from pathlib import Path

from dotenv import load_dotenv
from fastapi import UploadFile

load_dotenv()


# 支持的文件类型
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".tex"}

# 文件大小上限：默认 50MB，可通过环境变量 MAX_UPLOAD_MB 覆盖（单位 MB）
MAX_FILE_SIZE_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

# 文件存储目录
UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class FileService:
    """文件上传、存储、解析"""

    def __init__(self):
        # 内存存储文件元信息和解析内容
        self.files: Dict[str, Dict] = {}

    async def save_and_parse(self, file: UploadFile) -> Dict:
        """保存文件并解析内容

        Returns:
            {"file_id": str, "filename": str, "content_preview": str, "char_count": int}
        """
        # 校验文件类型
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {ext}。支持: {', '.join(ALLOWED_EXTENSIONS)}")

        # 读取文件内容
        content_bytes = await file.read()
        if len(content_bytes) > MAX_FILE_SIZE:
            raise ValueError(f"文件过大，最大支持 {MAX_FILE_SIZE // 1024 // 1024}MB")

        # 保存到磁盘
        file_id = str(uuid.uuid4())
        save_path = UPLOAD_DIR / f"{file_id}{ext}"
        save_path.write_bytes(content_bytes)

        # 解析文件内容
        text_content = self._parse_file(save_path, ext, content_bytes)

        # 存储元信息
        self.files[file_id] = {
            "file_id": file_id,
            "filename": filename,
            "extension": ext,
            "path": str(save_path),
            "content": text_content,
            "char_count": len(text_content),
        }

        return {
            "file_id": file_id,
            "filename": filename,
            "content_preview": text_content[:200] + "..." if len(text_content) > 200 else text_content,
            "char_count": len(text_content),
        }

    def get_file_content(self, file_id: str) -> Optional[str]:
        """根据 file_id 获取解析后的文本内容。"""
        file_info = self.files.get(file_id)
        return file_info["content"] if file_info else None

    def get_raw_path(self, file_id: str) -> Optional[str]:
        """根据 file_id 获取原始文件的磁盘路径（供需要原文件的 Agent 回取）。"""
        file_info = self.files.get(file_id)
        return file_info["path"] if file_info else None

    def get_file_info(self, file_id: str) -> Optional[Dict]:
        """获取对话所需的安全文件元信息和解析结果。"""
        file_info = self.files.get(file_id)
        if not file_info:
            return None
        content = file_info["content"]
        return {
            "id": file_info["file_id"],
            "name": file_info["filename"],
            "char_count": file_info["char_count"],
            "content": content,
            "preview": content[:200].replace("\n", " ").strip(),
        }

    def _parse_file(self, path: Path, ext: str, content_bytes: bytes) -> str:
        """根据文件类型解析文本"""
        if ext == ".txt":
            return self._parse_text(content_bytes)
        elif ext == ".md":
            return self._parse_text(content_bytes)
        elif ext == ".tex":
            return self._parse_text(content_bytes)
        elif ext == ".pdf":
            return self._parse_pdf(path)
        elif ext == ".docx":
            return self._parse_docx(path)
        else:
            return self._parse_text(content_bytes)

    def _parse_text(self, content_bytes: bytes) -> str:
        """解析纯文本文件"""
        # 尝试多种编码
        for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
            try:
                return content_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return content_bytes.decode("utf-8", errors="replace")

    def _parse_pdf(self, path: Path) -> str:
        """解析 PDF 文件"""
        try:
            import PyPDF2
            text_parts = []
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return "\n\n".join(text_parts)
        except ImportError:
            # PyPDF2 未安装，尝试 pdfplumber
            try:
                import pdfplumber
                text_parts = []
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                return "\n\n".join(text_parts)
            except ImportError:
                return "[PDF解析失败：请安装 PyPDF2 或 pdfplumber]"

    def _parse_docx(self, path: Path) -> str:
        """解析 DOCX 文件"""
        try:
            from docx import Document
            doc = Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)
        except ImportError:
            return "[DOCX解析失败：请安装 python-docx]"
