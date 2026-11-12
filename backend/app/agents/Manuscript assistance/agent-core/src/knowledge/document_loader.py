"""文档加载器 —— 支持多种格式的学术文档加载"""

from typing import List, Optional
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import config


class DocumentLoader:
    """学术文档加载器，支持PDF、Markdown、纯文本"""

    # 支持的文件扩展名
    SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".tex"}

    def __init__(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size or config.vector_store.chunk_size,
            chunk_overlap=chunk_overlap or config.vector_store.chunk_overlap,
            separators=[
                "\n\n",          # 段落
                "\n",            # 换行
                "\\section{",    # LaTeX章节
                "\\subsection{", # LaTeX子章节
                ". ",            # 句子
                " ",             # 单词
                "",
            ],
        )

    def load_file(self, file_path: str) -> List[Document]:
        """加载单个文件

        Args:
            file_path: 文件路径

        Returns:
            文档对象列表
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件格式: {path.suffix}。"
                f"支持的格式: {self.SUPPORTED_EXTENSIONS}"
            )

        docs = self._load_by_type(path)
        return docs

    def load_directory(self, dir_path: str, recursive: bool = True) -> List[Document]:
        """加载目录下所有支持的文件

        Args:
            dir_path: 目录路径
            recursive: 是否递归搜索子目录

        Returns:
            所有文档对象列表
        """
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f"不是有效目录: {dir_path}")

        all_docs = []
        pattern = "**/*" if recursive else "*"

        for file_path in path.glob(pattern):
            if file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                try:
                    docs = self.load_file(str(file_path))
                    all_docs.extend(docs)
                except Exception as e:
                    print(f"警告: 加载 {file_path} 失败: {e}")

        return all_docs

    def load_and_split(self, file_paths: List[str]) -> List[Document]:
        """加载多个文件并分块

        Args:
            file_paths: 文件路径列表

        Returns:
            分块后的文档列表
        """
        all_docs = []
        for fp in file_paths:
            try:
                docs = self.load_file(fp)
                all_docs.extend(docs)
            except Exception as e:
                print(f"警告: 加载 {fp} 失败: {e}")

        # 分块
        chunks = self.text_splitter.split_documents(all_docs)
        return chunks

    def _load_by_type(self, path: Path) -> List[Document]:
        """根据文件类型选择合适的加载器"""
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            return self._load_pdf(path)
        elif suffix == ".md":
            return self._load_markdown(path)
        elif suffix == ".tex":
            return self._load_latex(path)
        else:
            return self._load_text(path)

    def _load_pdf(self, path: Path) -> List[Document]:
        """加载 PDF 文件"""
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(str(path))
        return loader.load()

    def _load_markdown(self, path: Path) -> List[Document]:
        """加载 Markdown 文件"""
        from langchain_community.document_loaders import UnstructuredMarkdownLoader
        loader = UnstructuredMarkdownLoader(str(path))
        return loader.load()

    def _load_latex(self, path: Path) -> List[Document]:
        """加载 LaTeX 文件"""
        content = path.read_text(encoding="utf-8")
        return [Document(
            page_content=content,
            metadata={"source": str(path), "type": "latex"},
        )]

    def _load_text(self, path: Path) -> List[Document]:
        """加载纯文本文件"""
        from langchain_community.document_loaders import TextLoader
        loader = TextLoader(str(path), encoding="utf-8")
        return loader.load()
