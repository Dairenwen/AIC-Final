/*
 * How each agent, tool and skill is presented.
 *
 * The backend owns what exists (code, route, readiness).  This file owns how
 * it is shown: one glyph per entry and the wording a person reads.  Keeping
 * the wording here, rather than spread over thirty views, is what lets it be
 * edited as one piece of writing.
 *
 * Writing rules for anything added here:
 *   - say what the person provides and what they get back;
 *   - name things the way a researcher would, not by the framework or model
 *     that implements them;
 *   - one plain sentence beats a list of capabilities.
 */
import type { GlyphName } from '@/components/icons/glyphs'
import type { CatalogItem } from '@/types'

export interface AgentPresentation {
  icon: GlyphName
  /** One line under the agent name in its page header. */
  tagline: string
  /** What the card in the catalogue says. */
  summary: string
  /** The question the empty workspace opens with. */
  heading: string
  /** What happens next, in one or two sentences. */
  lead: string
}

export interface ToolPresentation {
  icon: GlyphName
  tagline: string
  summary: string
}

export const agentPresentation: Record<string, AgentPresentation> = {
  literature_search: {
    icon: 'literature',
    tagline: '按主题查找并整理文献',
    summary: '输入研究主题，检索平台文献库和公开学术源，得到文献清单、综述报告和按年份整理的脉络图。',
    heading: '想查哪个方向的文献？',
    lead: '输入研究主题后，会检索平台文献库和公开学术源，去重排序，再给出文献清单、综述报告和按年份整理的脉络图。',
  },
  manuscript_assistance: {
    icon: 'manuscript',
    tagline: '起草和润色论文章节',
    summary: '说明论文主题和要写的章节，生成摘要、引言、相关工作等部分的初稿，也可以润色已有段落。',
    heading: '这次要写哪一部分？',
    lead: '说明论文主题和需要的章节，例如摘要、引言、相关工作或实验方案，会生成一份可以接着修改的初稿。',
  },
  innovation_point_generation: {
    icon: 'innovation',
    tagline: '从已有文献里找还没人做的方向',
    summary: '梳理一个方向的研究趋势和尚未解决的问题，提出带评分和文献依据的创新点。',
    heading: '你关注哪个研究方向？',
    lead: '会阅读文献库里的相关论文，梳理研究趋势和尚未解决的问题，提出若干创新点，每一条都附评分和文献依据。',
  },
  paper_reading: {
    icon: 'reading',
    tagline: '逐节读懂一篇论文',
    summary: '上传 PDF 或给出 arXiv 链接，得到一份标注原文页码的精读报告，读完还可以继续提问。',
    heading: '要读哪篇论文？',
    lead: '上传 PDF 或粘贴 arXiv 链接。精读报告会讲清论文的问题、方法、实验、贡献和不足，每个结论都标出原文页码。',
  },
  patent_drafting: {
    icon: 'patent',
    tagline: '把技术方案写成专利申请材料',
    summary: '从技术方案出发，挑选专利点、检索现有技术，生成技术交底书和权利要求书草案。',
    heading: '先描述你的技术方案',
    lead: '会先列出候选专利点供你挑选，再检索现有技术、比对差异，最后生成技术交底书和权利要求书草案。',
  },
  academic_compliance: {
    icon: 'compliance',
    tagline: '投稿前的规范自查',
    summary: '检查稿件的写作规范、引用、图表对应和投稿格式，逐项给出风险等级和修改建议。',
    heading: '上传要检查的稿件',
    lead: '检查写作规范、引用与参考文献、图表编号是否对应，以及是否符合投稿格式，逐项给出风险等级和修改建议。',
  },
  arxiv_daily: {
    icon: 'daily',
    tagline: '每天的 arXiv 新论文',
    summary: '按分类浏览当天的 arXiv 计算机论文，附中文标题和摘要，可以直接打开原文 PDF。',
    heading: '今天有哪些新论文？',
    lead: '选择一个分类，查看当天发布的论文。',
  },
  academic_figure: {
    icon: 'figure',
    tagline: '用实验数据画论文插图',
    summary: '上传实验数据或草图，生成论文插图，并附图注和可以重新运行的绘图代码。',
    heading: '准备画一张什么图？',
    lead: '上传实验数据，也可以附上论文片段或手绘草图。除了图片，还会给出图注、整理后的数据和可以重新运行的绘图代码。',
  },
  academic_translation: {
    icon: 'translation',
    tagline: '论文和研究文档翻译',
    summary: '翻译论文和研究文档，术语前后一致，公式、引用和数值原样保留，可输出双语对照。',
    heading: '上传要翻译的文档',
    lead: '翻译时术语前后保持一致，公式、引用编号和数值原样保留。可以选择保留 PDF 版式，或输出双语对照。',
  },
  reviewer_comments: {
    icon: 'reviewer',
    tagline: '整理审稿意见并起草回复',
    summary: '把审稿意见拆成逐条问题，给出修改思路，起草回复信和返修清单。',
    heading: '粘贴收到的审稿意见',
    lead: '会把意见拆成逐条问题，标出类型和轻重缓急，给出修改思路，并起草回复信和返修清单。',
  },
  contribution_recommendation: {
    icon: 'recommend',
    tagline: '挑选合适的期刊和会议',
    summary: '根据论文的主题和完成度，分三档推荐期刊和会议，并列出投稿前要准备的事项。',
    heading: '介绍一下你的论文',
    lead: '填写标题、摘要和关键词，会按「可以冲一冲」「比较匹配」「相对稳妥」三档推荐期刊和会议，并列出投稿前要准备的材料。',
  },
}

export const toolPresentation: Record<string, ToolPresentation> = {
  markdown_to_docx: {
    icon: 'docx',
    tagline: '导出为 Word 文档',
    summary: '把 Markdown 文稿导出为 Word 文档，标题、列表、代码、引用和表格都会保留。',
  },
  formula_image_to_latex: {
    icon: 'formula',
    tagline: '识别图片里的公式',
    summary: '上传公式截图或扫描件，识别成可以复制和编辑的 LaTeX 代码。',
  },
  text_statistics: {
    icon: 'textStats',
    tagline: '字数、句长和高频词',
    summary: '统计中英文字词数、句子和段落数、平均句长、预计阅读时间和高频词。',
  },
  citation_formatter: {
    icon: 'citation',
    tagline: '生成参考文献条目',
    summary: '填入论文信息，同时得到 BibTeX、APA 和 GB/T 7714 三种格式的参考文献条目。',
  },
  table_converter: {
    icon: 'table',
    tagline: 'CSV 转论文表格',
    summary: '把 CSV 或 TSV 数据转成 Markdown 表格和 LaTeX 三线表。',
  },
  literature_ppt: {
    icon: 'slides',
    tagline: '把论文做成汇报幻灯片',
    summary: '上传一篇论文 PDF，生成一份用于组会汇报的幻灯片。',
  },
}

/** Tool routes map to tool codes so a tool page can find its own entry. */
const toolCodeByRoute: Record<string, string> = {
  '/tools/markdown-to-docx': 'markdown_to_docx',
  '/tools/formula-to-latex': 'formula_image_to_latex',
  '/tools/text-statistics': 'text_statistics',
  '/tools/citation-formatter': 'citation_formatter',
  '/tools/table-converter': 'table_converter',
  '/tools/literature-ppt': 'literature_ppt',
}

export function toolPresentationForRoute(path: string): ToolPresentation | undefined {
  return toolPresentation[toolCodeByRoute[path] ?? '']
}

/*
 * Skills are uploaded content, so their names and descriptions are shown as
 * written.  Only the glyph is chosen here, from what the skill is about.
 * Order matters: the first family whose keywords match wins.
 */
const skillFamilies: Array<{ icon: GlyphName; words: string[] }> = [
  { icon: 'ethics', words: ['ethic', '伦理', '诚信', '不端'] },
  { icon: 'reviewer', words: ['review-revision', '审稿', '返修', 'revision', 'reviewer'] },
  { icon: 'radar', words: ['monitor', '监测', '推送', '追踪'] },
  { icon: 'layout', words: ['template', '模板', '排版', 'latex'] },
  { icon: 'flow', words: ['plan', '方案', '实验设计', 'pipeline', 'workflow'] },
  { icon: 'innovation', words: ['idea', '创新', 'novelty', '选题'] },
  { icon: 'graph', words: ['analysis', '分析', '综述', 'survey'] },
  { icon: 'globe', words: ['web', '网页', 'browser', 'crawl'] },
  { icon: 'compass', words: ['guide', '指北', '顾问', 'lab', '入口'] },
  { icon: 'manuscript', words: ['writing', '写作', '润色', 'paper-writing'] },
  { icon: 'literature', words: ['search', '检索', 'literature', '文献', 'research'] },
]

export function skillIcon(item: Pick<CatalogItem, 'name' | 'description' | 'tags'>): GlyphName {
  const name = item.name.toLowerCase()
  const byName = skillFamilies.find((family) => family.words.some((word) => name.includes(word)))
  if (byName) return byName.icon
  const text = `${(item.tags ?? []).join(' ')} ${item.description}`.toLowerCase()
  return skillFamilies.find((family) => family.words.some((word) => text.includes(word)))?.icon ?? 'hexagon'
}

/** Category labels arrive in mixed languages; show them in one. */
const categoryLabels: Record<string, string> = {
  development: '开发',
  research: '科研',
  writing: '写作',
  general: '通用',
}

export function categoryLabel(value: string | undefined): string {
  if (!value) return ''
  return categoryLabels[value.toLowerCase()] ?? value
}

/** Review states of a skill, as a person would say them. */
const statusLabels: Record<string, string> = {
  APPROVED: '已上架',
  PENDING: '审核中',
  REJECTED: '未通过',
  DRAFT: '草稿',
  DISABLED: '已停用',
  ACTIVE: '启用中',
  INACTIVE: '未启用',
  FAILED: '失败',
  COMPLETED: '已完成',
  RUNNING: '运行中',
  QUEUED: '排队中',
  CANCELLED: '已取消',
}

export function statusLabel(value: string | undefined): string {
  if (!value) return ''
  return statusLabels[value.toUpperCase()] ?? value
}

/** Task type codes used by history and the admin tables. */
const taskTypeLabels: Record<string, string> = {
  LITERATURE_SEARCH: '文献检索',
  MANUSCRIPT_ASSISTANCE: '文稿辅助',
  INNOVATION_POINT_GENERATION: '创新点生成',
  PAPER_READING: '论文精读',
  PATENT_DRAFTING: '专利撰写',
  ACADEMIC_COMPLIANCE: '学术合规性检测',
  ARXIV_DAILY: '学术速递',
  ACADEMIC_FIGURE: '绘图创作',
  ACADEMIC_TRANSLATION: '学术翻译',
  REVIEWER_COMMENTS: '审稿意见回复',
  CONTRIBUTION_RECOMMENDATION: '投稿推荐',
  AGENT_TEAM: '智囊团',
  CHAT: '对话',
  RAG_ANSWER: '知识库问答',
}

export function taskTypeLabel(value: string | undefined): string {
  if (!value) return ''
  return taskTypeLabels[value.toUpperCase()] ?? value
}

export function agentIcon(code: string | undefined): GlyphName {
  return agentPresentation[code ?? '']?.icon ?? 'agents'
}

export function toolIcon(code: string | undefined): GlyphName {
  return toolPresentation[code ?? '']?.icon ?? 'tools'
}
