AGENTS = [
    {"id": "literature", "name": "文献检索", "category": "检索", "description": "跨来源检索论文，聚合去重并给出可追溯结果。", "status": "可用"},
    {"id": "digest", "name": "学术速递", "category": "检索", "description": "根据研究主题持续追踪新论文与学术动态。", "status": "可用"},
    {"id": "reading", "name": "论文精读", "category": "研读", "description": "解析论文结构，提炼方法、实验、结论和局限。", "status": "可用"},
    {"id": "figure", "name": "绘图创作", "category": "生成", "description": "从数据或描述生成规范、可复现的科研图表。", "status": "可用"},
    {"id": "writing", "name": "文稿辅助", "category": "生成", "description": "辅助组织论文内容、润色表达并检查引用位置。", "status": "可用"},
    {"id": "innovation", "name": "创新挖掘", "category": "洞察", "description": "对比研究脉络，识别空白、冲突与潜在研究问题。", "status": "试用"},
    {"id": "patent", "name": "专利文书", "category": "生成", "description": "辅助生成交底书、权利要求与技术实施描述。", "status": "可用"},
    {"id": "translation", "name": "学术翻译", "category": "生成", "description": "处理中英文专业术语、公式上下文与学术语体。", "status": "可用"},
    {"id": "compliance", "name": "学术合规校验", "category": "投稿", "description": "检查格式、引用、声明和常见学术规范风险。", "status": "可用"},
]

AGENT_TEAMS = [
    {"id": "survey", "name": "领域调研智囊团", "description": "检索、精读、脉络归纳与研究空白分析协同执行。", "members": ["文献检索", "论文精读", "创新挖掘"], "status": "可用"},
    {"id": "submission", "name": "论文投稿智囊团", "description": "文稿优化、合规检查、投稿推荐与回复建议。", "members": ["文稿辅助", "学术合规校验", "投稿推荐"], "status": "可用"},
]

TOOLS = [
    {"id": "chart", "name": "数据可视化", "category": "可视化", "description": "生成折线图、柱状图、散点图和论文组合图。"},
    {"id": "format", "name": "文档格式转换", "category": "转换", "description": "支持 Markdown、Word、PDF 和 LaTeX 常用格式转换。"},
    {"id": "citation", "name": "引文格式化", "category": "内容", "description": "输出 GB/T 7714、APA、BibTeX 等引用格式。"},
    {"id": "table", "name": "表格清洗", "category": "数据", "description": "检查缺失值、异常值并生成清洗摘要。"},
]

SKILLS = [
    {"id": "quick-read", "name": "论文快速阅读", "description": "结构化输出研究问题、方法、实验、局限和阅读建议。", "downloads": 328, "tags": ["论文", "精读"]},
    {"id": "review-reply", "name": "审稿意见回复", "description": "分类审稿意见，生成逐条回应结构和证据检查项。", "downloads": 214, "tags": ["投稿", "回复"]},
    {"id": "experiment-plan", "name": "实验设计检查", "description": "检查变量、基线、消融、统计显著性与可复现信息。", "downloads": 179, "tags": ["实验", "评测"]},
]

KNOWLEDGE_BASES = [
    {"id": "kb-1", "name": "多智能体研究", "documents": 42, "datasets": 3, "tags": ["Agent", "RAG"], "updatedAt": "今天 15:24"},
    {"id": "kb-2", "name": "学术检索与推荐", "documents": 28, "datasets": 1, "tags": ["检索", "推荐"], "updatedAt": "昨天 21:08"},
    {"id": "kb-3", "name": "论文绘图规范", "documents": 16, "datasets": 5, "tags": ["绘图", "可视化"], "updatedAt": "7月20日"},
]

HISTORY = [
    {"id": "h-1", "title": "多智能体科研助手综述", "time": "16:12"},
    {"id": "h-2", "title": "RAG 评测指标整理", "time": "昨天"},
    {"id": "h-3", "title": "实验结果折线图", "time": "7月20日"},
]

PROFILE = {
    "id": "dc0810d4-6f1e-4138-aa57-40da35637f34",
    "name": "whut2025202843",
    "organization": "武汉理工大学",
    "role": "normal_user",
    "plan": "科研基础版",
    "modelConfigured": True,
}

