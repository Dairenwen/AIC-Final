"""Document Assistant —— 主入口"""

import asyncio
import argparse
from typing import Optional

from .config import config
from .orchestrator import OrchestratorAgent
from .utils import get_logger
from .utils.helpers import format_section_output

logger = get_logger(__name__)


async def run_paper_agent(
    user_input: str,
    topic: Optional[str] = None,
    keywords: Optional[list] = None,
    contributions: Optional[list] = None,
    target_section: Optional[str] = None,
    language: str = "en",
    output_format: str = "markdown",
) -> str:
    """运行论文写作Agent

    Args:
        user_input: 用户输入描述
        topic: 论文主题
        keywords: 关键词列表
        contributions: 贡献点列表
        target_section: 目标章节（None则生成全文）
        language: 输出语言 (en/zh)
        output_format: 输出格式 (markdown/latex)

    Returns:
        生成的论文内容
    """
    logger.info(f"启动 Document Assistant | 主题: {topic or user_input[:50]}")

    orchestrator = OrchestratorAgent()

    result = await orchestrator.run(
        user_input=user_input,
        topic=topic or user_input,
        keywords=keywords or [],
        contributions=contributions or [],
        target_section=target_section,
        language=language,
    )

    # 格式化输出
    output_parts = []
    sections = result.get("sections", {})

    # 按顺序组装
    section_order = [
        "abstract", "introduction", "related_work",
        "method", "experiment", "conclusion"
    ]

    for section_name in section_order:
        if section_name in sections:
            content = sections[section_name].get("content", "")
            formatted = format_section_output(section_name, content, output_format)
            output_parts.append(formatted)

    full_output = "\n\n".join(output_parts)

    logger.info(f"论文生成完成 | 总字数: {len(full_output.split())}")
    return full_output


def interactive_mode():
    """交互式CLI模式"""
    print("=" * 60)
    print("  Document Assistant v0.1")
    print("  基于 LangChain + LangGraph 多Agent协作系统")
    print("=" * 60)
    print()
    print("命令:")
    print("  /help     - 显示帮助")
    print("  /config   - 显示当前配置")
    print("  /quit     - 退出")
    print()

    while True:
        try:
            user_input = input("\n📝 请输入您的需求 > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见!")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            print("再见!")
            break
        if user_input == "/help":
            _print_help()
            continue
        if user_input == "/config":
            _print_config()
            continue

        # 收集额外信息
        print("\n(以下为可选信息，直接回车跳过)")
        topic = input("  论文主题: ").strip() or None
        keywords_str = input("  关键词(逗号分隔): ").strip()
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()] if keywords_str else None
        language = input("  语言(en/zh, 默认en): ").strip() or "en"

        print("\n⏳ 正在生成论文，请稍候...\n")

        try:
            result = asyncio.run(run_paper_agent(
                user_input=user_input,
                topic=topic,
                keywords=keywords,
                language=language,
            ))
            print("\n" + "=" * 60)
            print(result)
            print("=" * 60)
        except Exception as e:
            logger.error(f"生成失败: {e}")
            print(f"\n❌ 生成过程中出错: {e}")
            print("请检查 API 配置或网络连接。")


def _print_help():
    """打印帮助信息"""
    print("""
使用方式：
  1. 输入论文写作需求的自然语言描述
  2. Agent会自动识别意图并生成对应内容

示例输入：
  - "帮我写一篇关于基于Transformer的图像分类方法的论文"
  - "写一下引言部分，研究主题是多模态情感分析"
  - "帮我生成相关工作部分，关键词：知识图谱、推荐系统"
  - "润色以下段落：..."

支持的章节：
  摘要(abstract) | 引言(introduction) | 相关工作(related_work)
  方法(method) | 实验(experiment) | 总结(conclusion)
""")


def _print_config():
    """打印当前配置"""
    print(f"""
当前配置：
  LLM模型: {config.llm.model}
  Embedding模型: {config.embedding.model}
  向量库类型: {config.vector_store.store_type}
  最大迭代次数: {config.agent.max_iterations}
  质量阈值: {config.agent.quality_threshold}
  RAG启用: {config.agent.enable_rag}
  默认语言: {config.agent.language}
""")


def main():
    """CLI入口"""
    parser = argparse.ArgumentParser(description="Document Assistant")
    parser.add_argument("--input", "-i", type=str, help="直接输入写作需求")
    parser.add_argument("--topic", "-t", type=str, help="论文主题")
    parser.add_argument("--keywords", "-k", type=str, help="关键词(逗号分隔)")
    parser.add_argument("--section", "-s", type=str, help="目标章节")
    parser.add_argument("--language", "-l", type=str, default="en", help="输出语言")
    parser.add_argument("--format", "-f", type=str, default="markdown", help="输出格式")
    parser.add_argument("--interactive", action="store_true", help="交互模式")

    args = parser.parse_args()

    if args.interactive or not args.input:
        interactive_mode()
    else:
        keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
        result = asyncio.run(run_paper_agent(
            user_input=args.input,
            topic=args.topic,
            keywords=keywords,
            target_section=args.section,
            language=args.language,
            output_format=args.format,
        ))
        print(result)


if __name__ == "__main__":
    main()
