"""RAG 检索工具 —— 基于用户上传文档的语义检索"""

from typing import List, Optional
from pathlib import Path

from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import config


class RAGManager:
    """RAG 向量检索管理器"""

    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            model=config.embedding.model,
            api_key=config.embedding.api_key,
        )
        self.vector_store: Optional[FAISS] = None
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.vector_store.chunk_size,
            chunk_overlap=config.vector_store.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def load_documents(self, file_paths: List[str]) -> int:
        """加载文档到向量数据库

        Args:
            file_paths: 文件路径列表（支持 PDF、TXT、MD）

        Returns:
            加载的文档块数量
        """
        from langchain_community.document_loaders import (
            PyPDFLoader,
            TextLoader,
            UnstructuredMarkdownLoader,
        )

        all_docs = []

        for path_str in file_paths:
            path = Path(path_str)
            if not path.exists():
                continue

            try:
                if path.suffix.lower() == ".pdf":
                    loader = PyPDFLoader(str(path))
                elif path.suffix.lower() == ".md":
                    loader = UnstructuredMarkdownLoader(str(path))
                else:
                    loader = TextLoader(str(path), encoding="utf-8")

                docs = loader.load()
                all_docs.extend(docs)
            except Exception as e:
                print(f"加载文件失败 {path}: {e}")

        if not all_docs:
            return 0

        # 分块
        chunks = self.text_splitter.split_documents(all_docs)

        # 创建或更新向量库
        if self.vector_store is None:
            self.vector_store = FAISS.from_documents(chunks, self.embeddings)
        else:
            self.vector_store.add_documents(chunks)

        return len(chunks)

    def search(self, query: str, top_k: int = 5) -> List[Document]:
        """语义检索

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            相关文档块列表
        """
        if self.vector_store is None:
            return []

        results = self.vector_store.similarity_search(query, k=top_k)
        return results

    def save(self, path: Optional[str] = None):
        """持久化向量库"""
        save_path = path or config.vector_store.store_path
        if self.vector_store:
            self.vector_store.save_local(save_path)

    def load(self, path: Optional[str] = None):
        """加载已有向量库"""
        load_path = path or config.vector_store.store_path
        if Path(load_path).exists():
            self.vector_store = FAISS.load_local(
                load_path, self.embeddings, allow_dangerous_deserialization=True
            )


# 全局 RAG 管理器实例
rag_manager = RAGManager()


@tool
def rag_search(query: str, top_k: int = 5) -> str:
    """
    从用户上传的参考文献中进行语义检索。

    Args:
        query: 检索查询（自然语言）
        top_k: 返回的最相关文档块数量

    Returns:
        相关文档内容
    """
    results = rag_manager.search(query, top_k)
    if not results:
        return "未找到相关内容。请确保已上传参考文献。"

    output_parts = []
    for i, doc in enumerate(results, 1):
        source = doc.metadata.get("source", "未知来源")
        output_parts.append(f"[{i}] 来源: {source}\n{doc.page_content}\n")

    return "\n---\n".join(output_parts)


@tool
def load_references(file_paths: List[str]) -> str:
    """
    加载参考文献到RAG检索系统。

    Args:
        file_paths: 文件路径列表

    Returns:
        加载结果信息
    """
    count = rag_manager.load_documents(file_paths)
    if count > 0:
        rag_manager.save()
        return f"成功加载 {count} 个文档块到检索系统。"
    return "未成功加载任何文档，请检查文件路径。"


RAGRetrievalTool = [rag_search, load_references]
