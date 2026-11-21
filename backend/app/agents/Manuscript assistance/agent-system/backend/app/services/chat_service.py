"""聊天服务 —— 意图识别 + 关键词抽取 + ArXiv检索 + 文稿解析总结 + 流式输出 + 持久化

核心流程（让思考过程真实且随请求变化）：
1. 分析请求：用 LLM 判断意图，并把用户诉求转成「英文学术检索词」
2. 文稿解析（若有文件）：解析文本 + LLM 生成中文理解总结，展示给用户确认
3. 文献检索：无论是否上传文件，只要涉及某个研究领域就先检索 arXiv 真实文献
4. 组织回复：基于检索到的真实文献给出建议 / 润色
"""

import os
import re
import json
import asyncio
from typing import Optional, List, Dict, AsyncGenerator
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# 持久化存储目录
DATA_DIR = Path(os.path.dirname(__file__)).parent.parent / "data"
CONVERSATIONS_FILE = DATA_DIR / "conversations.json"


# ===== 意图类型 =====
INTENT_POLISH = "polish"          # 用户提供了内容，需要润色
INTENT_GENERATE = "generate"      # 用户要求生成，但没提供初稿
INTENT_FILE_POLISH = "file_polish" # 用户上传了文件，需要解析后润色
INTENT_QUESTION = "question"      # 普通问答 / 领域咨询


# ===== 系统提示词 =====
ANALYSIS_PROMPT = """你是学术写作助手的「请求分析模块」。分析用户输入，判断意图，并把用户诉求转换成用于 arXiv 英文文献检索的学术关键词。

意图类型：
- "polish"：用户提供了成段文字（初稿/段落），希望润色或修改
- "generate"：用户要求撰写/生成某内容，但没有提供初稿素材
- "question"：用户在咨询某研究领域、写作方法或概念

请只返回一个 JSON（不要额外解释）：
{
  "intent": "polish | generate | question",
  "field": "研究领域的中文简称（例如：检索增强生成 RAG）；若与学术领域无关则填空字符串",
  "topic_en": "用于 arXiv 检索的英文主题短语（规范学术术语，3-8 个词）",
  "keywords": ["英文关键词1", "英文关键词2", "英文关键词3"],
  "search_needed": true 或 false
}

要求：
- topic_en 与 keywords 必须是规范英文学术术语，即使用户用中文提问也要翻译成英文。
- 关键词要具体（如 "retrieval-augmented generation" 而非 "AI"）。
- 仅当输入是纯寒暄/闲聊/与研究无关时，search_needed 才为 false。
"""

DOC_SUMMARY_PROMPT = """你是学术文献分析助手。下面是用户上传文档的内容（可能是论文或写作初稿）。请完成两件事：

1. 用 3-5 句中文，准确总结该文档的：研究主题、采用的方法、主要贡献/结论。目的是让用户确认「你对文档的理解是否正确」。
2. 提取用于 arXiv 英文文献检索的主题与关键词。

请只返回一个 JSON（不要额外解释）：
{
  "summary": "中文总结，3-5 句，具体到该文档的主题/方法/贡献",
  "topic_en": "英文检索主题短语",
  "keywords": ["英文关键词1", "英文关键词2", "英文关键词3"]
}
"""

WRITING_SYSTEM_PROMPT = """你是一位专业的学术论文写作助手「Document Assistant」。

核心原则：
- 你是辅助工具，不是代笔。你的工作是在用户现有内容基础上进行润色、优化和改进。
- 永远不要凭空捏造内容。所有修改都应基于用户提供的素材。
- 如果需要补充学术背景，请基于检索到的真实文献。

写作规范：
- 使用正式学术语言，逻辑连贯。
- 保持用户原有的核心观点和论述方向。
- 引用格式使用 [Author et al., Year]。
- 指出原文的不足并解释修改原因。
"""

# 针对「文件润色」的追加指令：先复述理解，再给建议
FILE_POLISH_INSTRUCTION = """本次用户上传了文档。请按以下结构回复：
1. 先用「📄 文档理解」小标题，简要复述你对文档核心内容（主题/方法/贡献）的理解，让用户确认解析是否准确。
2. 再给出针对性的润色建议或优化方案。
"""

# 针对「生成请求但无初稿」：基于真实文献给建议并引导补充素材
FOLLOWUP_PROMPT = """用户希望你帮忙撰写/生成内容，但还没有提供初稿。

请按以下结构回复：
1. 先基于下方检索到的真实文献，梳理该主题在写作时应覆盖的要点、常见结构或方法（并用 [Author et al., Year] 引用文献，让建议有据可依）。
2. 再礼貌地请用户补充其**具体**的方法、数据、创新点或已有草稿——说明「基于你的真实素材润色」比「凭空生成」更准确、更贴合你的工作。

语气亲切专业。不要凭空替用户编造实验数据或结论。
"""


class ChatService:
    """聊天服务：分析 + 检索 + 解析总结 + 流式输出 + JSON持久化"""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_API_BASE", None),
        )
        self.model = os.getenv("LLM_MODEL", "qwen-plus")

        # 确保数据目录存在
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 从文件加载会话
        self.conversations: Dict[str, Dict] = self._load_conversations()

    def _load_conversations(self) -> Dict[str, Dict]:
        """从 JSON 文件加载会话数据"""
        if CONVERSATIONS_FILE.exists():
            try:
                with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_conversations(self):
        """将会话数据保存到 JSON 文件"""
        try:
            with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.conversations, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[WARNING] 保存会话数据失败: {e}")

    async def stream_response(
        self,
        message: str,
        conversation_id: str,
        agent_id: str = "writing",
        file_records: Optional[List[Dict]] = None,
        topic: Optional[str] = None,
        keywords: Optional[list] = None,
        target_section: Optional[str] = None,
        language: str = "zh",
    ) -> AsyncGenerator[str, None]:
        """流式返回响应（SSE 格式）"""

        # 获取或创建会话
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = {
                "id": conversation_id,
                "title": message[:30] or "新对话",
                "agent_id": agent_id,
                "messages": [],
                "created_at": datetime.now().isoformat(),
            }
            self._save_conversations()

        conv = self.conversations[conversation_id]

        # 记录本次请求的思考步骤
        thinking_steps: List[Dict] = []
        has_file = bool(file_records)

        # ===== Step 1: 意图识别 / 请求分析 =====
        yield self._sse_event("status", {"step": "intent", "label": "意图识别"})

        search_topic: Optional[str] = topic
        search_terms: List[str] = list(keywords) if keywords else []
        field_label = ""

        if has_file:
            intent = INTENT_FILE_POLISH
            file_names = [r["name"] for r in file_records]
            intent_detail = f"识别为「文稿润色」，检测到 {len(file_records)} 个上传文件：{', '.join(file_names)}"
        else:
            analysis = await self._analyze_request(message)
            intent = analysis.get("intent") or self._detect_intent_rule(message)
            field_label = (analysis.get("field") or "").strip()
            search_topic = analysis.get("topic_en") or search_topic or message[:80]
            if analysis.get("keywords"):
                search_terms = analysis["keywords"]
            intent_label_map = {
                INTENT_POLISH: "文稿润色",
                INTENT_GENERATE: "生成请求（无初稿）",
                INTENT_QUESTION: "领域咨询",
            }
            label_zh = intent_label_map.get(intent, "写作咨询")
            if field_label:
                intent_detail = f"识别为「{label_zh}」，研究领域：{field_label}"
            else:
                intent_detail = f"识别为「{label_zh}」"

        thinking_steps.append({"key": "intent", "label": "意图识别", "detail": intent_detail})
        yield self._sse_event("status", {
            "step": "intent_done", "label": "意图识别",
            "intent": intent, "detail": intent_detail,
        })

        # ===== Step 2: 文稿解析（仅文件） =====
        file_context: Optional[str] = None
        file_summary: Optional[str] = None
        if has_file:
            yield self._sse_event("status", {"step": "parsing", "label": "文稿解析"})

            file_contents = [r["content"] for r in file_records]
            file_context = "\n\n---\n\n".join(file_contents)

            # LLM 生成文档理解总结 + 检索关键词
            doc_info = await self._summarize_document(file_context)
            file_summary = doc_info.get("summary") or "（未能生成摘要，将基于原文处理）"
            if doc_info.get("topic_en"):
                search_topic = doc_info["topic_en"]
            if doc_info.get("keywords"):
                search_terms = doc_info["keywords"]

            file_names = [r["name"] for r in file_records]
            total_chars = len(file_context)
            parsing_detail = (
                f"已解析 {len(file_records)} 个文件（{', '.join(file_names)}），共 {total_chars} 字符。"
                f"内容理解：{file_summary}"
            )
            thinking_steps.append({"key": "parsing", "label": "文稿解析", "detail": parsing_detail})
            yield self._sse_event("status", {
                "step": "parsing_done", "label": "文稿解析", "detail": parsing_detail,
            })

        # ===== Step 3: 文献检索（领域相关即检索，真实数据支撑建议） =====
        search_needed = has_file or intent in (INTENT_POLISH, INTENT_GENERATE) or (
            not has_file and self._should_search(message, field_label, search_terms)
        )
        search_results_text: Optional[str] = None
        papers: List[Dict] = []

        if search_needed:
            kw_preview = "、".join(search_terms[:4]) if search_terms else (search_topic or "")
            yield self._sse_event("status", {
                "step": "searching", "label": "文献检索",
                "detail": f"正在以关键词检索 arXiv：{kw_preview}",
            })

            papers = await self._search_references(search_topic, search_terms, message)

            if papers:
                search_results_text = self._format_refs_for_prompt(papers)
                search_detail = self._format_search_detail(papers, search_terms, search_topic)
            else:
                kw_show = kw_preview or "相关主题"
                search_detail = (
                    f"以「{kw_show}」检索 arXiv 暂未命中合适文献，将基于既有学术知识作答，"
                    f"并建议你手动核对最新文献。"
                )
            thinking_steps.append({"key": "searching", "label": "文献检索", "detail": search_detail})
            yield self._sse_event("status", {
                "step": "searching_done", "label": "文献检索", "detail": search_detail,
            })

        # ===== Step 4: 组织回复 =====
        if intent == INTENT_GENERATE:
            gen_label = "组织建议"
            if papers:
                gen_detail = f"结合检索到的 {len(papers)} 篇文献，梳理写作要点并引导补充素材"
            else:
                gen_detail = "梳理写作要点结构，并引导你补充真实素材"
            yield self._sse_event("status", {"step": "generating", "label": gen_label, "detail": gen_detail})
            thinking_steps.append({"key": "generating", "label": gen_label, "detail": gen_detail})

            async for chunk in self._followup_stream(conv, message, search_results_text, language, thinking_steps):
                yield chunk

        else:
            # file_polish / polish / question
            if has_file:
                gen_label = "润色修改"
                enhanced_message = (
                    f"以下是用户上传文档的内容：\n\n{file_context[:6000]}\n\n"
                    f"你对文档的理解摘要：{file_summary}\n\n"
                    f"用户要求：{message}"
                )
            elif intent == INTENT_POLISH:
                gen_label = "润色修改"
                enhanced_message = message
            else:
                gen_label = "组织回复"
                enhanced_message = message

            if papers:
                gen_detail = f"结合 {len(papers)} 篇真实文献与上下文，生成专业回复"
            else:
                gen_detail = "结合上下文组织专业回复"
            yield self._sse_event("status", {"step": "generating", "label": gen_label, "detail": gen_detail})
            thinking_steps.append({"key": "generating", "label": gen_label, "detail": gen_detail})

            async for chunk in self._generate_stream(
                conv, enhanced_message, search_results_text, language, thinking_steps,
                is_file=has_file, display_message=message,
            ):
                yield chunk

        yield self._sse_event("done", {"conversation_id": conversation_id})

    # ===================== 分析 / 解析 =====================

    async def _analyze_request(self, message: str) -> Dict:
        """用 LLM 分析意图并抽取英文检索关键词；失败时回退规则。"""
        result = await self._llm_json(ANALYSIS_PROMPT, message, max_tokens=400)
        if result and isinstance(result, dict) and result.get("intent"):
            # 规整字段
            result.setdefault("keywords", [])
            if not isinstance(result["keywords"], list):
                result["keywords"] = []
            result.setdefault("search_needed", True)
            return result
        # 回退：规则判断意图，无英文关键词
        return {
            "intent": self._detect_intent_rule(message),
            "field": "",
            "topic_en": "",
            "keywords": [],
            "search_needed": True,
        }

    async def _summarize_document(self, file_context: str) -> Dict:
        """LLM 总结文档内容 + 抽取检索关键词。"""
        # 取文档正文前后各一部分，兼顾标题/摘要与正文
        head = file_context[:6000]
        result = await self._llm_json(DOC_SUMMARY_PROMPT, head, max_tokens=600)
        if result and isinstance(result, dict) and result.get("summary"):
            result.setdefault("keywords", [])
            if not isinstance(result["keywords"], list):
                result["keywords"] = []
            return result
        # 回退：截取开头作为“摘要”
        preview = re.sub(r"\s+", " ", file_context[:200]).strip()
        return {"summary": f"文档开头预览：{preview}...", "topic_en": "", "keywords": []}

    def _detect_intent_rule(self, message: str) -> str:
        """规则版意图识别（LLM 不可用时的回退）。"""
        msg_len = len(message)
        if msg_len > 200:
            return INTENT_POLISH
        generate_keywords = ["帮我写", "帮我生成", "撰写", "生成一篇", "写一篇", "写一个", "帮我做"]
        if any(kw in message for kw in generate_keywords) and msg_len < 150:
            return INTENT_GENERATE
        polish_keywords = ["润色", "修改", "改进", "优化", "帮我改"]
        if any(kw in message for kw in polish_keywords):
            return INTENT_POLISH
        return INTENT_QUESTION

    def _should_search(self, message: str, field_label: str, terms: List[str]) -> bool:
        """判断问答类请求是否需要检索文献。"""
        if field_label or terms:
            return True
        # 纯寒暄/过短
        greetings = ["你好", "您好", "hi", "hello", "谢谢", "感谢", "在吗", "怎么用", "你是谁"]
        stripped = message.strip().lower()
        if len(stripped) <= 4 or any(g in stripped for g in greetings):
            return False
        return True

    # ===================== 文献检索 =====================

    async def _search_references(
        self,
        topic_en: Optional[str],
        keywords: Optional[List[str]],
        message: str,
    ) -> List[Dict]:
        """调用 ArXiv 检索，返回论文结构化列表。"""
        try:
            from .arxiv_search import search_arxiv

            terms = list(keywords) if keywords else []
            if topic_en and topic_en not in terms:
                # 把主题短语也作为一个关键词加入
                terms = [topic_en] + terms

            # 在线程池执行同步网络请求，避免阻塞事件循环
            papers = await asyncio.to_thread(
                search_arxiv,
                query=message[:120],
                max_results=5,
                terms=terms,
            )
            return papers or []
        except Exception:
            return []

    def _format_refs_for_prompt(self, papers: List[Dict]) -> str:
        """把论文列表格式化为提示词中的参考文献块。"""
        refs = []
        for p in papers:
            authors = p.get("authors", [])
            author_str = authors[0].split()[-1] if authors else "Unknown"
            if len(authors) > 1:
                author_str += " et al."
            year = p.get("year", "") or ""
            title = p.get("title", "")
            url = p.get("url", "")
            abstract = p.get("abstract", "")
            refs.append(f"- [{author_str}, {year}] {title}\n  摘要: {abstract}\n  链接: {url}")
        return "以下是检索到的真实 arXiv 文献（请据此给出有据可依的建议）：\n" + "\n".join(refs)

    def _format_search_detail(self, papers: List[Dict], keywords: List[str], topic_en: Optional[str]) -> str:
        """构建文献检索步骤的动态详情。"""
        kw_str = "、".join(keywords[:4]) if keywords else (topic_en or "")
        titles = "；".join(f"《{p.get('title', '')[:48]}》" for p in papers[:3])
        prefix = f"检索词：{kw_str}。" if kw_str else ""
        return f"{prefix}从 arXiv 命中 {len(papers)} 篇相关文献：{titles}"

    # ===================== LLM 调用 =====================

    async def _llm_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> Optional[Dict]:
        """调用 LLM 并解析返回的 JSON。"""
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
            content = (resp.choices[0].message.content or "").strip()
            # 去掉可能的 ```json 包裹
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
            match = re.search(r"\{.*\}", content, re.S)
            if match:
                return json.loads(match.group(0))
        except Exception:
            return None
        return None

    async def _generate_stream(
        self,
        conv: Dict,
        message: str,
        search_results: Optional[str],
        language: str,
        thinking_steps: Optional[List[Dict]] = None,
        is_file: bool = False,
        display_message: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """流式生成回复"""
        system_prompt = WRITING_SYSTEM_PROMPT
        if is_file:
            system_prompt += "\n\n" + FILE_POLISH_INSTRUCTION
        system_prompt += "\n\n请使用英文回复。" if language == "en" else "\n\n请使用中文回复。"

        if search_results:
            system_prompt += (
                f"\n\n{search_results}\n\n"
                "请在回复中适当引用上述文献（使用 [Author et al., Year] 格式），"
                "并在回复末尾附上「参考文献」列表（含论文标题和 arXiv 链接）。"
                "不要编造未提供的文献。"
            )

        messages = [{"role": "system", "content": system_prompt}]
        for msg in conv["messages"][-16:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        # 存入会话历史时用用户真实输入，避免把注入的文件正文写进记录
        store_message = display_message if display_message is not None else message
        async for chunk in self._stream_and_save(conv, messages, store_message, thinking_steps):
            yield chunk

    async def _followup_stream(
        self,
        conv: Dict,
        message: str,
        search_results: Optional[str],
        language: str,
        thinking_steps: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """生成请求但无初稿：基于真实文献给建议并引导补充素材。"""
        system_prompt = WRITING_SYSTEM_PROMPT + "\n\n" + FOLLOWUP_PROMPT
        system_prompt += "\n请使用英文回复。" if language == "en" else "\n请使用中文回复。"

        if search_results:
            system_prompt += (
                f"\n\n{search_results}\n\n"
                "上述是检索到的真实文献，请据此给出有据可依的写作要点，"
                "并在末尾附「参考文献」列表（含标题与 arXiv 链接）。不要编造文献。"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        async for chunk in self._stream_and_save(conv, messages, message, thinking_steps):
            yield chunk

    async def _stream_and_save(
        self,
        conv: Dict,
        messages: List[Dict],
        user_message: str,
        thinking_steps: Optional[List[Dict]],
    ) -> AsyncGenerator[str, None]:
        """统一的流式调用 + 保存逻辑。"""
        full_response = ""
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        full_response += delta.content
                        yield self._sse_event("token", {"content": delta.content})
                        await asyncio.sleep(0)
        except Exception as e:
            yield self._sse_event("token", {"content": f"\n\n⚠️ 生成出错: {str(e)}"})

        conv["messages"].append({
            "role": "user",
            "content": user_message,
            "thinking_steps": thinking_steps or [],
        })
        conv["messages"].append({"role": "assistant", "content": full_response})
        self._save_conversations()

    def _sse_event(self, event: str, data: dict) -> str:
        """构造 SSE 事件"""
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def get_conversations(self) -> List[Dict]:
        """获取会话列表"""
        result = []
        for conv in self.conversations.values():
            result.append({
                "id": conv["id"],
                "title": conv["title"],
                "agent_id": conv.get("agent_id", "writing"),
                "created_at": conv["created_at"],
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    def get_messages(self, conversation_id: str) -> Optional[List[Dict]]:
        """获取指定会话的消息列表"""
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return None
        return conv.get("messages", [])

    def delete_conversation(self, conversation_id: str):
        """删除会话"""
        self.conversations.pop(conversation_id, None)
        self._save_conversations()
