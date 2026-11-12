"""向量数据库管理"""

from typing import List, Optional
from pathlib import Path

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

from ..config import config


class VectorStoreManager:
    """向量数据库管理器 —— 统一管理文档的向量化存储和检索"""

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = Path(store_path or config.vector_store.store_path)
        self.embeddings = OpenAIEmbeddings(
            model=config.embedding.model,
            api_key=config.embedding.api_key,
        )
        self._store: Optional[FAISS] = None

    @property
    def store(self) -> Optional[FAISS]:
        """懒加载向量库"""
        if self._store is None and self.store_path.exists():
            self._store = FAISS.load_local(
                str(self.store_path),
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
        return self._store

    def create_from_documents(self, documents: List[Document]) -> None:
        """从文档列表创建向量库"""
        if not documents:
            return
        self._store = FAISS.from_documents(documents, self.embeddings)
        self.save()

    def add_documents(self, documents: List[Document]) -> None:
        """追加文档到现有向量库"""
        if not documents:
            return
        if self._store is None:
            self.create_from_documents(documents)
        else:
            self._store.add_documents(documents)
            self.save()

    def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Document]:
        """语义相似度检索

        Args:
            query: 查询文本
            top_k: 返回数量
            score_threshold: 相似度阈值（可选）

        Returns:
            按相似度排序的文档列表
        """
        if self.store is None:
            return []

        if score_threshold is not None:
            results = self.store.similarity_search_with_score(query, k=top_k)
            return [doc for doc, score in results if score <= score_threshold]
        else:
            return self.store.similarity_search(query, k=top_k)

    def search_with_scores(self, query: str, top_k: int = 5) -> List[tuple]:
        """带分数的语义检索"""
        if self.store is None:
            return []
        return self.store.similarity_search_with_score(query, k=top_k)

    def save(self) -> None:
        """持久化到磁盘"""
        if self._store:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store.save_local(str(self.store_path))

    def clear(self) -> None:
        """清空向量库"""
        self._store = None
        if self.store_path.exists():
            import shutil
            shutil.rmtree(self.store_path)

    @property
    def doc_count(self) -> int:
        """当前文档数量"""
        if self.store is None:
            return 0
        return self.store.index.ntotal
