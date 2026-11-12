"""引用管理工具 —— 生成 BibTeX 引用"""

from typing import Optional
from langchain_core.tools import tool


@tool
def generate_bibtex(
    title: str,
    authors: str,
    year: int,
    venue: str,
    entry_type: str = "inproceedings",
    doi: Optional[str] = None,
    pages: Optional[str] = None,
    volume: Optional[str] = None,
) -> str:
    """
    生成 BibTeX 格式的引用条目。

    Args:
        title: 论文标题
        authors: 作者（用 "and" 连接）
        year: 发表年份
        venue: 发表场所（期刊/会议名）
        entry_type: 条目类型 (article / inproceedings / misc)
        doi: DOI号
        pages: 页码
        volume: 卷号

    Returns:
        BibTeX 格式的引用字符串
    """
    # 生成 cite key: 第一作者姓 + 年份 + 标题第一个实词
    first_author_last = authors.split(",")[0].split()[-1].lower()
    title_word = title.split()[0].lower()
    cite_key = f"{first_author_last}{year}{title_word}"

    lines = [f"@{entry_type}{{{cite_key},"]
    lines.append(f"  title = {{{title}}},")
    lines.append(f"  author = {{{authors}}},")
    lines.append(f"  year = {{{year}}},")

    if entry_type == "article":
        lines.append(f"  journal = {{{venue}}},")
    else:
        lines.append(f"  booktitle = {{{venue}}},")

    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if pages:
        lines.append(f"  pages = {{{pages}}},")
    if volume:
        lines.append(f"  volume = {{{volume}}},")

    lines.append("}")

    return "\n".join(lines)


@tool
def search_doi(title: str) -> str:
    """
    通过论文标题查询 DOI。

    Args:
        title: 论文标题

    Returns:
        DOI 信息或未找到提示
    """
    import requests

    url = "https://api.crossref.org/works"
    params = {"query.title": title, "rows": 1}

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            return (
                f"DOI: {item.get('DOI', 'N/A')}\n"
                f"Title: {item.get('title', ['N/A'])[0]}\n"
                f"Publisher: {item.get('publisher', 'N/A')}"
            )
        return f"未找到标题为 '{title}' 的论文 DOI。"

    except Exception as e:
        return f"DOI查询失败: {str(e)}"


@tool
def format_citation_inline(author: str, year: int) -> str:
    """
    生成行内引用格式。

    Args:
        author: 第一作者姓氏
        year: 年份

    Returns:
        格式化的行内引用
    """
    return f"[{author} et al., {year}]"


CitationTool = [generate_bibtex, search_doi, format_citation_inline]
