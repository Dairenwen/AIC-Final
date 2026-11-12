"""全局配置管理"""

import os
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class LLMConfig(BaseModel):
    """LLM 配置"""
    model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4"))
    api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    api_base: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_BASE"))
    temperature: float = 0.7
    max_tokens: int = 4096
    request_timeout: int = 120


class EmbeddingConfig(BaseModel):
    """Embedding 配置"""
    model: str = Field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))


class VectorStoreConfig(BaseModel):
    """向量数据库配置"""
    store_type: str = Field(default_factory=lambda: os.getenv("VECTOR_STORE_TYPE", "faiss"))
    store_path: str = Field(default_factory=lambda: os.getenv("VECTOR_STORE_PATH", "./data/vector_store"))
    chunk_size: int = 1000
    chunk_overlap: int = 200


class AgentConfig(BaseModel):
    """Agent 行为配置"""
    max_iterations: int = 3          # 单章节最大迭代次数
    quality_threshold: float = 0.7   # 质量评分阈值
    enable_rag: bool = True          # 是否启用RAG
    language: str = "en"             # 默认输出语言
    verbose: bool = True             # 是否输出详细日志


class AppConfig(BaseModel):
    """应用总配置"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


# 全局配置单例
config = AppConfig()
