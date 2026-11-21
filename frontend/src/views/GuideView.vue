<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { agentPresentation, toolPresentation } from '@/catalog/presentation'
import type { GlyphName } from '@/components/icons/glyphs'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import PageHeader from '@/components/PageHeader.vue'
import { authState } from '@/auth/session'

interface Step {
  title: string
  body: string
}

interface Section {
  id: string
  icon: GlyphName
  title: string
  lead: string
  to?: string
  toLabel?: string
  steps?: Step[]
  tips?: string[]
}

interface Entry {
  icon: GlyphName
  name: string
  to: string
  give: string
  get: string
}

const isAdmin = authState.user?.role === 'system_admin'

const sections: Section[] = [
  {
    id: 'start',
    icon: 'compass',
    title: '快速开始',
    lead: '智研把查文献、读论文、写文稿、做翻译等科研环节放在同一个工作台里。第一次使用，按下面三步走一遍就能上手。',
    steps: [
      {
        title: '登录或注册',
        body: '用手机号和密码登录；没有账号时在登录页切换到“注册”，填写姓名、手机号、密码和确认密码（密码至少 8 位，学校或机构选填，开启短信验证时还需填写验证码）。',
      },
      {
        title: '认识界面',
        body: '左侧是导航栏：上方是功能入口，中间是“最近的任务”，底部是你的账号。右侧是当前页面的内容。点左上角“新建任务”随时回到首页对话框。',
      },
      {
        title: '提出第一个问题',
        body: '在首页对话框里直接输入研究问题并回车，回答会边生成边显示。需要更专门的处理时，再从“我的智能体”里选对应的智能体。',
      },
    ],
    tips: ['“最近的任务”可以点标题收起或展开，点其中一条会回到那次任务的结果页。', '首页右上角的太阳/月亮按钮用来切换浅色和深色界面。'],
  },
  {
    id: 'chat',
    icon: 'message',
    title: '首页对话',
    to: '/',
    toLabel: '打开首页',
    lead: '首页对话框适合随手提问、让模型解释概念、改写段落，也可以把问题交给某个智能体或知识库来回答。',
    steps: [
      { title: '输入并发送', body: '输入问题后按 Enter 发送，Shift + Enter 换行。普通对话生成过程中，发送按钮会变成“停止”，点一下即可中断。' },
      {
        title: '用 + 号选择回答方式',
        body: '点输入框左下角的 + 号：选“知识库问答”会在“我的知识库”里的文献（本地上传的 PDF 和加入的平台文献）以及你有权限的平台文献里检索（管理员可检索整个平台文献库），再给出带出处的回答；选“智能体”或“智囊团”则把这次输入交给对应的智能体处理。',
      },
      { title: '添加附件', body: '点回形针按钮选择 PDF、Word(.docx)、TXT、Markdown、CSV 或 Excel 文件，发送后模型会结合文件内容回答，同一对话里继续追问也能看到这个文件；文件很长时只读取前面部分。如果用 + 号选了智能体（如论文精读、学术翻译），文件会交给该智能体处理。' },
      {
        title: '选择模型',
        body: '输入框下方的下拉框用来选模型：“API 对话模型”是平台默认模型，带“微调”字样的是平台的科研微调模型，“我的模型”分组里是你在个人中心添加并测试通过的模型。',
      },
    ],
    tips: [
      '回答里的公式、表格和代码会按格式渲染，可以直接复制。',
      '模型如果输出了思考过程，会折叠显示在回答上方，点开即可查看。',
      '知识库问答的回答下方会列出引用的文献片段，方便核对原文。',
    ],
  },
  {
    id: 'projects',
    icon: 'projects',
    title: '科研项目',
    to: '/projects',
    toLabel: '打开科研项目',
    lead: '一个项目对应一个课题，把这个课题的文档、对话和智能体任务放在一起，之后回来能接着做。',
    steps: [
      { title: '新建项目', body: '在科研项目页点“新建项目”，填写项目名称，研究目标和项目说明可以先空着。创建后点项目卡片进入工作区。' },
      {
        title: '左栏：项目资料',
        body: '显示研究目标、研究文档列表、最近任务和研究产物。点文档标题在中间打开，点“研究文档”标题右侧的小图标新建一篇研究笔记。',
      },
      { title: '中栏：文档编辑', body: '用 Markdown 写研究笔记，改完点“保存”，每次保存都会生成一个新版本号。' },
      {
        title: '右栏：项目助手',
        body: '用法和首页对话框相同，回答时会参考这个项目的研究目标、研究文档和最近的任务，对话记录保存在这个项目里。下方的“智能体”快捷入口会带着当前项目进入对应智能体，任务结果自动归到项目里。',
      },
      {
        title: '调整三栏宽度',
        body: '把鼠标移到两栏之间的分隔线上，按住左右拖动即可调整宽度；双击分隔线恢复默认宽度。宽度会记住，下次打开保持不变。',
      },
    ],
    tips: ['屏幕较窄时三栏会变成顶部的“项目资料 / 文档 / 项目助手”三个页签。', '只读成员可以查看项目内容，但不能编辑文档或发送消息。'],
  },
  {
    id: 'agents',
    icon: 'agents',
    title: '我的智能体',
    to: '/agents',
    toLabel: '打开我的智能体',
    lead: '每个智能体负责一类科研任务。进入智能体后在输入框里说明需求（部分智能体需要上传文件），任务会在后台执行并显示进度，完成后结果留在页面上；刷新页面后也能在左侧“最近的任务”里找到。',
  },
  {
    id: 'teams',
    icon: 'teams',
    title: '我的智囊团',
    to: '/teams',
    toLabel: '打开我的智囊团',
    lead: '智囊团把几个智能体按顺序串起来，前一位的结果自动交给后一位，适合“先调研、再找创新点、最后写初稿”这类较长的任务。',
    steps: [
      { title: '创建智囊团', body: '在“现成的组合”里点“用这个组合”作为起点，或点“创建智囊团”自己挑 2 到 8 个成员；填写名称（说明可选）后保存。' },
      { title: '调整顺序', body: '在“执行顺序”里用上移、下移调整成员的先后，顺序决定了结果的传递方向。' },
      { title: '执行任务', body: '选中一个智囊团，在“这次要完成什么”里输入任务（至少 10 个字）。执行过程会逐步显示每位成员的状态，点“查看结果”可以看该成员的产出；需要上传材料的成员会提示“补充材料”。最后给出总结。' },
    ],
  },
  {
    id: 'tools',
    icon: 'tools',
    title: '科研工具集',
    to: '/tools',
    toLabel: '打开科研工具集',
    lead: '工具集里是处理文档、公式、表格和引用格式的小工具，打开即用，结果可以直接复制或下载。',
  },
  {
    id: 'skills',
    icon: 'skills',
    title: '科研技能库',
    to: '/skills',
    toLabel: '打开科研技能库',
    lead: '技能库收录了可以交给智能体调用的方法和流程说明，例如文献调研流程、论文写作模板、审稿回复方法。',
    steps: [
      { title: '查找技能', body: '用右上角搜索框按名称或关键词查找，或者用“科研 / 开发”等分类筛选。' },
      { title: '查看详情', body: '点卡片进入详情页，可以看到技能简介、标签和完整内容；带文件目录的技能可以在左侧切换文件。' },
      { title: '使用技能', body: '点“复制内容”把技能说明复制下来，粘贴到首页对话框或智能体的输入里，模型就会按这套流程工作。' },
    ],
  },
  {
    id: 'knowledge',
    icon: 'knowledge',
    title: '我的知识库',
    to: '/academic-space/knowledge',
    toLabel: '打开我的知识库',
    lead: '知识库用来存放和整理你自己的文献，可以按课题分文件夹管理，也可以直接复用平台文献库里已有的论文。放进来的文献可以在首页“知识库问答”里被检索和引用：本地上传的 PDF 会自动建立检索，列表里显示“可用于知识库问答”后即可使用。',
    steps: [
      { title: '新建知识库', body: '点左侧的“新建知识库”，填写名称；可以选择上级知识库来建立子文件夹，按课题分层整理。' },
      {
        title: '上传本地文献',
        body: '点“上传本地文献”，选择 PDF（支持多选，单个文件不超过 50MB）。如果平台文献库里已有同一篇，会提示直接使用平台文献，省去重复保存；你的知识库里已有完全相同的文件时也会提示。',
      },
      {
        title: '从平台知识库加载',
        body: '点“从平台知识库加载”，按标题、作者、摘要或 arXiv 编号搜索平台已有文献，勾选后确认加载，直接复用平台已有的切片和检索数据。',
      },
      { title: '整理文献', body: '用搜索框按标题或期刊查找。本地上传的文献可以点下载按钮取回 PDF，平台文献可以点外链按钮打开来源；“更多操作”里可以移动到其他知识库或移除文献。' },
    ],
  },
  {
    id: 'daily',
    icon: 'daily',
    title: '学术速递',
    to: '/agents/academic-daily',
    toLabel: '打开学术速递',
    lead: '学术速递按分类展示 arXiv 计算机科学的最新论文，数据来自 arXiv 官方接口，中文标题、一句话总结和中文摘要由平台模型生成。',
    steps: [
      { title: '选择分类', body: '点“浏览 CS 分类”选择方向（如 cs.AI 人工智能、cs.CV 计算机视觉），选好后会自动开始同步；第一次进入也可以直接点“获取 cs.AI 的新论文”。' },
      {
        title: '等待同步',
        body: '同步时会显示进度条和“已完成 x / y 篇”，进度按实际完成的论文数推进。一小时内同步过的分类会直接显示上次结果；点“重新同步”可以强制刷新。',
      },
      {
        title: '阅读论文',
        body: '每张卡片有英文标题、中文标题、AI 总结和作者；点“摘要详情”展开中英文摘要，点“阅读原版 PDF”在页面内打开原文，点“arXiv 页面”跳到 arXiv。',
      },
      { title: '搜索', body: '分类按钮旁的搜索框可以在当前分类里按标题（含中文标题）、作者、摘要或 arXiv 编号筛选。' },
    ],
  },
  {
    id: 'profile',
    icon: 'user',
    title: '个人中心',
    to: '/profile',
    toLabel: '打开个人中心',
    lead: '点左下角的头像进入个人中心，这里管理账号资料、模型、密码和用量。',
    steps: [
      { title: '个人资料', body: '查看姓名、学校或机构、手机号、角色、套餐和账号 ID。姓名和机构来自注册时的填写，目前不能在这里修改。' },
      {
        title: '模型库',
        body: '查看平台提供的模型，或点“添加模型”接入自己的模型：选择提供商和模型类型，填写显示名称、模型名称、API Base URL 和 API Key（可调整请求超时和最大输出 Token），保存后会自动测试连接。测试通过的对话模型可以在对话里选用，也可以设为默认模型。',
      },
      { title: '修改密码', body: '输入当前密码和新密码（至少 8 位，且不能与当前密码相同）即可更新；更新后其他已登录的设备需要重新登录。' },
      { title: '用量', body: '查看本周期已用的模型 Token、总额度，以及平台模型和自有接口各自的消耗。' },
    ],
  },
]

const adminSection: Section = {
  id: 'admin',
  icon: 'database',
  title: '平台数据（管理员）',
  lead: '以下功能只对系统管理员显示，入口在左侧导航的“平台数据”分组和底部的“系统配置”。',
  steps: [
    {
      title: '知识库管理',
      body: '按研究领域查看平台文献库。可以新增文献、编辑元数据、删除文献；勾选还没切片的文献后点“切片”，文献会被切块并写入向量库，之后才能被语义检索到。“预览切片”可以查看一篇文献的切块结果。',
    },
    {
      title: '训练集生成',
      body: '用知识库里的切片生成 SFT 和 DPO 问答数据。先设置要生成的 Chunk 总数和各领域配比，点“按配比选择”选出切片，先生成 SFT，再基于 SFT 生成 DPO；结果在页面下方逐条接受、驳回或标为存疑，可以导出复核记录或下载 DPO JSONL。',
    },
    { title: '系统配置', body: '查看平台运行概览、知识库统计、异常记录、审计日志和权限配置。' },
  ],
}

const agentOrder = [
  'literature_search',
  'paper_reading',
  'manuscript_assistance',
  'innovation_point_generation',
  'academic_translation',
  'academic_figure',
  'academic_compliance',
  'reviewer_comments',
  'contribution_recommendation',
  'patent_drafting',
  'arxiv_daily',
]
const agentMeta: Record<string, { name: string; to: string; give: string }> = {
  literature_search: { name: '文献检索', to: '/agents/literature-search', give: '研究主题或关键词' },
  paper_reading: { name: '论文精读', to: '/agents/paper-reading', give: '论文 PDF 或 arXiv 链接' },
  manuscript_assistance: { name: '文稿辅助', to: '/agents/manuscript-assistance', give: '论文主题和要写的章节，或待润色的段落' },
  innovation_point_generation: { name: '创新点生成', to: '/agents/innovation-point-generation', give: '关注的研究方向' },
  academic_translation: { name: '学术翻译', to: '/agents/academic-translation', give: '论文或研究文档（MD、TXT、DOCX 或 PDF 文件）' },
  academic_figure: { name: '绘图创作', to: '/agents/academic-figure', give: '实验数据或草图，以及想要的图表类型' },
  academic_compliance: { name: '学术合规性检测', to: '/agents/academic-compliance', give: '待投稿的稿件' },
  reviewer_comments: { name: '审稿意见解析与引导回复', to: '/agents/reviewer-comments', give: '审稿意见原文' },
  contribution_recommendation: { name: '投稿推荐', to: '/agents/contribution-recommendation', give: '论文的主题、摘要和完成度' },
  patent_drafting: { name: '专利撰写', to: '/agents/patent-drafting', give: '技术方案说明' },
  arxiv_daily: { name: '学术速递', to: '/agents/academic-daily', give: '选择一个 arXiv 分类' },
}
const agentEntries: Entry[] = agentOrder.map((code) => ({
  icon: agentPresentation[code]?.icon ?? 'agents',
  name: agentMeta[code].name,
  to: agentMeta[code].to,
  give: agentMeta[code].give,
  get: agentPresentation[code]?.summary ?? '',
}))

const toolMeta: Array<{ code: string; name: string; to: string; give: string }> = [
  { code: 'formula_image_to_latex', name: '公式图片转 LaTeX', to: '/tools/formula-to-latex', give: '公式截图或扫描件' },
  { code: 'literature_ppt', name: '文献 PPT 绘制', to: '/tools/literature-ppt', give: '一篇论文 PDF' },
  { code: 'citation_formatter', name: '文献引用格式化', to: '/tools/citation-formatter', give: '论文的标题、作者、年份等信息' },
  { code: 'table_converter', name: '科研表格转换', to: '/tools/table-converter', give: 'CSV 或 TSV 数据' },
  { code: 'text_statistics', name: '学术文本统计', to: '/tools/text-statistics', give: '一段中文或英文文本' },
  { code: 'markdown_to_docx', name: 'Markdown 转 Word', to: '/tools/markdown-to-docx', give: 'Markdown 文稿' },
]
const toolEntries: Entry[] = toolMeta.map((item) => ({
  icon: toolPresentation[item.code]?.icon ?? 'tools',
  name: item.name,
  to: item.to,
  give: item.give,
  get: toolPresentation[item.code]?.summary ?? '',
}))

const faqs = [
  { q: '任务执行到一半关掉页面会丢吗？', a: '不会。智能体任务在后台执行，回来后从左侧“最近的任务”点开即可看到进度或结果。' },
  {
    q: '回答里引用的文献从哪里来？',
    a: '“知识库问答”会检索“我的知识库”里的文献和你有权限的平台文献，回答下方列出的片段都可以展开核对原文（本地 PDF 会标出页码）；文献检索等智能体还会检索公开学术源。普通对话不做检索，内容来自模型本身。',
  },
  {
    q: '添加自己的模型时测试连接失败怎么办？',
    a: '检查 API Base URL 是否填到 /chat/completions 之前的那一级（通常以 /v1 结尾）、模型名称是否与服务商提供的一致、API Key 是否有效；改完后在模型卡片上重新测试。',
  },
  {
    q: '学术速递里个别论文没有中文摘要？',
    a: '中文内容由模型逐篇生成，偶尔会有个别论文生成失败，页面会提示数量；点“重新同步”会重新获取论文列表，近期已生成过中文内容的论文会直接复用，只补上缺失的部分。',
  },
  {
    q: '上传文件有什么限制？',
    a: '对话附件支持 PDF、Word(.docx)、TXT、Markdown、CSV 和 Excel(.xlsx)，旧版 .doc 需另存为 .docx；智能体各有支持的格式。单个文件不超过 50MB（公式识别工具为 10MB），知识库上传单个 PDF 不超过 50MB。扫描版 PDF 没有文字层时，可能无法提取内容。',
  },
  {
    q: '看不到“知识库管理”和“训练集生成”？',
    a: '这两个板块只对系统管理员开放，普通账号的导航栏里不会显示。需要使用时请用管理员账号登录。',
  },
]

const allSections = isAdmin ? [...sections, adminSection] : sections
const toc = [...allSections.map((item) => ({ id: item.id, title: item.title })), { id: 'faq', title: '常见问题' }]
const activeId = ref(toc[0].id)
let observer: IntersectionObserver | null = null

function jump(id: string) {
  activeId.value = id
  document.getElementById(`guide-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(async () => {
  await nextTick()
  if (typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible[0]) activeId.value = visible[0].target.id.replace('guide-', '')
    },
    { rootMargin: '-10% 0px -70% 0px' },
  )
  toc.forEach((item) => {
    const node = document.getElementById(`guide-${item.id}`)
    if (node) observer?.observe(node)
  })
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div class="workspace-page guide-page">
    <PageHeader title="使用指南" description="从登录到每个功能的具体用法，按左侧目录查找需要的部分。">
      <RouterLink class="secondary-button" to="/"><ZyIcon name="home" :size="15" />返回首页</RouterLink>
    </PageHeader>

    <div class="guide-layout">
      <nav class="guide-toc" aria-label="指南目录">
        <span class="guide-toc__title">目录</span>
        <button v-for="(item, index) in toc" :key="item.id" type="button" :class="{ active: activeId === item.id }" @click="jump(item.id)">
          <i>{{ String(index + 1).padStart(2, '0') }}</i>{{ item.title }}
        </button>
      </nav>

      <div class="guide-body">
        <section v-for="section in allSections" :id="`guide-${section.id}`" :key="section.id" class="guide-section">
          <header class="guide-section__header">
            <span class="guide-section__icon"><ZyIcon :name="section.icon" :size="20" /></span>
            <h2>{{ section.title }}</h2>
            <RouterLink v-if="section.to" class="guide-section__link" :to="section.to">
              {{ section.toLabel }}<ZyIcon name="arrowRight" :size="14" />
            </RouterLink>
          </header>
          <p class="guide-lead">{{ section.lead }}</p>

          <ol v-if="section.steps" class="guide-steps">
            <li v-for="(step, index) in section.steps" :key="step.title">
              <span class="guide-steps__index">{{ index + 1 }}</span>
              <div>
                <strong>{{ step.title }}</strong>
                <p>{{ step.body }}</p>
              </div>
            </li>
          </ol>

          <div v-if="section.id === 'agents' || section.id === 'tools'" class="guide-table" role="table">
            <div class="guide-table__head" role="row">
              <span role="columnheader">{{ section.id === 'agents' ? '智能体' : '工具' }}</span>
              <span role="columnheader">你需要提供</span>
              <span role="columnheader">你会得到</span>
            </div>
            <RouterLink
              v-for="entry in section.id === 'agents' ? agentEntries : toolEntries"
              :key="entry.to"
              class="guide-table__row"
              role="row"
              :to="entry.to"
            >
              <span class="guide-table__name" role="cell"><ZyIcon :name="entry.icon" :size="17" />{{ entry.name }}</span>
              <span role="cell">{{ entry.give }}</span>
              <span role="cell">{{ entry.get }}</span>
            </RouterLink>
          </div>

          <ul v-if="section.tips" class="guide-tips">
            <li v-for="tip in section.tips" :key="tip">{{ tip }}</li>
          </ul>
        </section>

        <section id="guide-faq" class="guide-section">
          <header class="guide-section__header">
            <span class="guide-section__icon"><ZyIcon name="help" :size="20" /></span>
            <h2>常见问题</h2>
          </header>
          <details v-for="item in faqs" :key="item.q" class="guide-faq">
            <summary>{{ item.q }}<ZyIcon name="chevronDown" :size="15" /></summary>
            <p>{{ item.a }}</p>
          </details>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.guide-layout {
  display: grid;
  grid-template-columns: 13.5rem minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

.guide-toc {
  position: sticky;
  top: var(--space-5);
  display: grid;
  gap: 0.125rem;
  padding: var(--space-3);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-panel);
}

.guide-toc__title {
  padding: var(--space-1) var(--space-3) var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
  letter-spacing: 0.04em;
}

.guide-toc button {
  display: flex;
  min-height: 2.25rem;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-3);
  color: var(--text-secondary);
  background: transparent;
  border-radius: var(--radius-control, 0.5rem);
  font-size: var(--type-footnote-size);
  text-align: left;
  transition:
    background var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.guide-toc button i {
  color: var(--text-disabled);
  font-size: var(--type-caption-size);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.guide-toc button:hover {
  color: var(--text-primary);
  background: var(--bg-sunken);
}

.guide-toc button.active {
  color: var(--accent-text);
  background: var(--accent-tint);
  font-weight: var(--weight-semibold);
}

.guide-toc button.active i {
  color: var(--accent-text);
}

.guide-body {
  display: grid;
  min-width: 0;
  gap: var(--space-5);
}

.guide-section {
  padding: var(--space-6);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-panel);
  scroll-margin-top: var(--space-5);
}

.guide-section__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.guide-section__icon {
  display: inline-flex;
  width: 2.25rem;
  height: 2.25rem;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  color: var(--accent-text);
  background: var(--accent-tint);
  border-radius: 0.625rem;
}

.guide-section__header h2 {
  margin: 0;
  font-size: var(--type-title3-size, 1.25rem);
  line-height: 1.3;
}

.guide-section__link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
  color: var(--accent-text);
  font-size: var(--type-footnote-size);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

.guide-lead {
  max-width: 72ch;
  margin: var(--space-4) 0 0;
  color: var(--text-secondary);
  font-size: var(--type-callout-size);
  line-height: 1.75;
}

.guide-steps {
  display: grid;
  gap: var(--space-3);
  margin: var(--space-5) 0 0;
  padding: 0;
  list-style: none;
}

.guide-steps li {
  display: grid;
  grid-template-columns: 1.75rem minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--bg-sunken);
  border-radius: 0.75rem;
}

.guide-steps__index {
  display: inline-flex;
  width: 1.75rem;
  height: 1.75rem;
  align-items: center;
  justify-content: center;
  color: var(--accent-text);
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 50%;
  font-size: var(--type-footnote-size);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.guide-steps strong {
  display: block;
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  line-height: 1.75rem;
}

.guide-steps p {
  margin: var(--space-1) 0 0;
  color: var(--text-secondary);
  font-size: var(--type-footnote-size);
  line-height: 1.75;
}

.guide-table {
  display: grid;
  margin-top: var(--space-5);
  overflow: hidden;
  border: 1px solid var(--border-hairline);
  border-radius: 0.75rem;
}

.guide-table__head,
.guide-table__row {
  display: grid;
  grid-template-columns: minmax(10rem, 1.1fr) minmax(0, 1.4fr) minmax(0, 2.6fr);
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  font-size: var(--type-footnote-size);
  line-height: 1.65;
}

.guide-table__head {
  color: var(--text-tertiary);
  background: var(--bg-sunken);
  font-size: var(--type-caption-size);
}

.guide-table__row {
  color: var(--text-secondary);
  border-top: 1px solid var(--border-hairline);
  transition: background var(--duration-base) var(--ease-standard);
}

.guide-table__row:hover {
  background: var(--bg-sunken);
}

.guide-table__name {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: var(--text-primary);
  font-weight: var(--weight-semibold);
}

.guide-table__name .zy-icon {
  flex: 0 0 auto;
  margin-top: 0.2rem;
  color: var(--accent-text);
}

.guide-tips {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-5) 0 0;
  padding: var(--space-4) var(--space-4) var(--space-4) var(--space-6);
  color: var(--text-secondary);
  border-left: 3px solid var(--accent-border, var(--border-default));
  background: var(--accent-tint);
  border-radius: 0 0.75rem 0.75rem 0;
  font-size: var(--type-footnote-size);
  line-height: 1.7;
}

.guide-faq {
  margin-top: var(--space-3);
  border: 1px solid var(--border-hairline);
  border-radius: 0.75rem;
}

.guide-faq:first-of-type {
  margin-top: var(--space-5);
}

.guide-faq summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  font-weight: var(--weight-semibold);
  cursor: pointer;
  list-style: none;
}

.guide-faq summary::-webkit-details-marker {
  display: none;
}

.guide-faq summary .zy-icon {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  transition: transform var(--duration-base) var(--ease-standard);
}

.guide-faq[open] summary .zy-icon {
  transform: rotate(180deg);
}

.guide-faq p {
  margin: 0;
  padding: 0 var(--space-4) var(--space-4);
  color: var(--text-secondary);
  font-size: var(--type-footnote-size);
  line-height: 1.75;
}

@media (max-width: 960px) {
  .guide-layout {
    grid-template-columns: 1fr;
  }

  .guide-toc {
    position: static;
    display: flex;
    gap: var(--space-1);
    overflow-x: auto;
  }

  .guide-toc__title {
    display: none;
  }

  .guide-toc button {
    flex: 0 0 auto;
  }

  .guide-table__head {
    display: none;
  }

  .guide-table__row {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }

  .guide-section {
    padding: var(--space-4);
  }
}
</style>
