"""模型服务层 — 多模型协同：主推理 + 嵌入 + 重排"""
from typing import Optional
from config import ModelConfig
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelService:
    """统一模型服务接口，兼容 Anthropic / OpenAI API"""

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig()

    def chat(self, messages: list[dict], model: Optional[str] = None,
             temperature: float = 0.3, max_tokens: int = 4096, json_mode: bool = False) -> str:
        model_name = model or self.config.primary_model
        try:
            import requests
            is_anthropic = "anthropic" in self.config.api_base.lower()
            if is_anthropic:
                return self._chat_anthropic(messages, model_name, temperature, max_tokens)
            else:
                return self._chat_openai(messages, model_name, temperature, max_tokens, json_mode)
        except Exception as e:
            logger.error(f"模型调用失败: {e}")
            raise

    def _chat_anthropic(self, messages, model, temperature, max_tokens):
        import requests
        system_msg = ""
        chat_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                chat_msgs.append({"role": m["role"], "content": m["content"]})
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": chat_msgs}
        if system_msg:
            body["system"] = system_msg
        resp = requests.post(f"{self.config.api_base}/v1/messages",
                             headers={"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01",
                                      "Content-Type": "application/json"}, json=body, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # 兼容多种 Anthropic-compatible API 响应格式
        # DeepSeek 可能返回多个 content block（thinking + text）
        content = data.get("content", [])
        if isinstance(content, str):
            return content
        if isinstance(content, list) and len(content) > 0:
            # 优先找 type="text" 的 block
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
            # 回退：取第一个 block
            block = content[0]
            if isinstance(block, str):
                return block
            if isinstance(block, dict):
                return block.get("text") or block.get("content") or str(block)
        # 回退：尝试 OpenAI 格式
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        raise KeyError(f"无法解析响应 content: {list(data.keys())}")

    def _chat_openai(self, messages, model, temperature, max_tokens, json_mode):
        import requests
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = requests.post(f"{self.config.api_base}/v1/chat/completions",
                             headers={"Authorization": f"Bearer {self.config.api_key}",
                                      "Content-Type": "application/json"}, json=body, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def embed(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        model_name = model or self.config.embedding_model
        try:
            from sentence_transformers import SentenceTransformer
            if not hasattr(self, "_local_embed_model"):
                self._local_embed_model = SentenceTransformer(model_name)
            return self._local_embed_model.encode(texts, normalize_embeddings=True).tolist()
        except ImportError:
            return self._embed_api(texts, model_name)

    def _embed_api(self, texts, model):
        import requests
        resp = requests.post(f"{self.config.api_base}/v1/embeddings",
                             headers={"Authorization": f"Bearer {self.config.api_key}",
                                      "Content-Type": "application/json"},
                             json={"model": model, "input": texts}, timeout=60)
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def rerank(self, query: str, documents: list[str], top_k: int = 10,
               model: Optional[str] = None) -> list[dict]:
        model_name = model or self.config.reranker_model
        try:
            from FlagEmbedding import FlagReranker
            if not hasattr(self, "_local_reranker"):
                self._local_reranker = FlagReranker(model_name, use_fp16=True)
            pairs = [[query, doc] for doc in documents]
            scores = self._local_reranker.compute_score(pairs)
            if isinstance(scores, float):
                scores = [scores]
            ranked = sorted([{"index": i, "score": float(s), "text": documents[i]}
                             for i, s in enumerate(scores)], key=lambda x: x["score"], reverse=True)
            return ranked[:top_k]
        except ImportError:
            return self._rerank_api(query, documents, top_k, model_name)

    def _rerank_api(self, query, documents, top_k, model):
        import requests
        resp = requests.post(f"{self.config.api_base}/v1/rerank",
                             headers={"Authorization": f"Bearer {self.config.api_key}",
                                      "Content-Type": "application/json"},
                             json={"model": model, "query": query, "documents": documents, "top_n": top_k},
                             timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return [{"index": r["index"], "score": r["relevance_score"], "text": documents[r["index"]]}
                for r in data["results"]]


_model_service: Optional[ModelService] = None


def get_model_service(config: Optional[ModelConfig] = None) -> ModelService:
    global _model_service
    if _model_service is None or (
        config is not None
        and (
            _model_service.config.api_base != config.api_base
            or _model_service.config.primary_model != config.primary_model
            or _model_service.config.api_key != config.api_key
        )
    ):
        _model_service = ModelService(config)
    return _model_service
