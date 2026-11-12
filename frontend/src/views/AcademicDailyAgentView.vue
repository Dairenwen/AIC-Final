<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  BookOpenCheck,
  CalendarDays,
  ChevronDown,
  Database,
  Download,
  ExternalLink,
  FileText,
  Landmark,
  Layers3,
  LoaderCircle,
  RefreshCw,
  Search,
  Sparkles,
  UserRound,
  X,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import type { ArxivDailyCategory, ArxivDailyPaper, ResearchTask } from '@/types'

interface TaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
}

const route = useRoute()
const router = useRouter()
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const activeCode = ref('cs.AI')
const searchQuery = ref('')
const categoryMenuOpen = ref(false)
const busy = ref(false)
const errorMessage = ref('')
const readerPaper = ref<ArxivDailyPaper | null>(null)
let closeEvents: (() => void) | null = null

const fallbackCategories: ArxivDailyCategory[] = [
  ['cs.AI', '人工智能'], ['cs.AR', '硬件架构'], ['cs.CC', '计算复杂性'], ['cs.CE', '计算工程、金融与科学'],
  ['cs.CG', '计算几何'], ['cs.CL', '计算与语言'], ['cs.CR', '密码学与安全'], ['cs.CV', '计算机视觉与模式识别'],
  ['cs.CY', '计算机与社会'], ['cs.DB', '数据库'], ['cs.DC', '分布式、并行与集群计算'], ['cs.DL', '数字图书馆'],
  ['cs.DM', '离散数学'], ['cs.DS', '数据结构与算法'], ['cs.ET', '新兴技术'], ['cs.FL', '形式语言与自动机'],
  ['cs.GL', '综合文献'], ['cs.GR', '图形学'], ['cs.GT', '计算机科学与博弈论'], ['cs.HC', '人机交互'],
  ['cs.IR', '信息检索'], ['cs.IT', '信息论'], ['cs.LG', '机器学习'], ['cs.LO', '计算机逻辑'],
  ['cs.MA', '多智能体系统'], ['cs.MM', '多媒体'], ['cs.MS', '数学软件'], ['cs.NA', '数值分析'],
  ['cs.NE', '神经与进化计算'], ['cs.NI', '网络与互联网架构'], ['cs.OH', '其他计算机科学'], ['cs.OS', '操作系统'],
  ['cs.PF', '性能分析'], ['cs.PL', '编程语言'], ['cs.RO', '机器人学'], ['cs.SC', '符号计算'],
  ['cs.SD', '声音技术'], ['cs.SE', '软件工程'], ['cs.SI', '社会与信息网络'], ['cs.SY', '系统与控制'],
].map(([code, name_cn]) => ({ code, name_cn }))

const output = computed(() => task.value?.output ?? {})
const categories = computed(() => {
  const source = output.value.daily_categories?.filter((item) => item.code !== 'cs') ?? []
  return source.length ? source : fallbackCategories
})
const papers = computed(() => output.value.daily_papers ?? [])
const summary = computed(() => output.value.daily_summary ?? {})
const warnings = computed(() => output.value.daily_warnings ?? [])
const activeCategory = computed(() => categories.value.find((item) => item.code === activeCode.value)
  ?? { code: activeCode.value, name_cn: output.value.daily_request?.category_name || '人工智能' })
const filteredPapers = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return papers.value
  return papers.value.filter((paper) => [
    paper.title, paper.title_cn, paper.summary_cn, paper.abstract_cn, paper.abstract,
    paper.authors, paper.affiliations?.join(' '), paper.categories?.join(' '), paper.arxiv_id,
  ].some((value) => String(value || '').toLowerCase().includes(keyword)))
})
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const today = computed(() => new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'short',
}).format(new Date()))
const fetchedTime = computed(() => summary.value.fetched_at
  ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(summary.value.fetched_at))
  : '等待同步')
const pdfUrl = computed(() => readerPaper.value?.pdf_url
  ? apiUrl(`/academic-daily/pdf?source=${encodeURIComponent(readerPaper.value.pdf_url)}`)
  : '')

async function startFeed(category = activeCode.value, refresh = false) {
  if (busy.value || isRunning.value) return
  activeCode.value = category
  categoryMenuOpen.value = false
  readerPaper.value = null
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const categoryName = categories.value.find((item) => item.code === category)?.name_cn || category
    const response = await http.post('/tasks', {
      prompt: `同步 ${category} ${categoryName} 的每日最新论文，并保留中英文摘要与原版 PDF 入口`,
      agent_code: 'arxiv_daily',
      model: 'source',
      arxiv_category: category,
      arxiv_refresh: refresh,
    })
    task.value = response.data.data as ResearchTask
    await router.replace({ path: route.path, query: { task: task.value.id } })
    subscribe(task.value.id)
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    busy.value = false
  }
}

async function loadTask(taskId: string) {
  errorMessage.value = ''
  try {
    const response = await http.get(`/tasks/${taskId}`)
    task.value = response.data.data as ResearchTask
    const category = task.value.output?.daily_request?.category
    if (category) activeCode.value = category
    if (isRunning.value) subscribe(taskId)
  } catch (error) {
    task.value = null
    errorMessage.value = requestError(error)
  }
}

async function refreshTask() {
  if (!task.value) return
  const response = await http.get(`/tasks/${task.value.id}`)
  task.value = response.data.data as ResearchTask
  const category = task.value.output?.daily_request?.category
  if (category) activeCode.value = category
}

function subscribe(taskId: string) {
  closeEvents?.()
  const source = new EventSource(apiUrl(`/tasks/${taskId}/events`))
  const eventTypes = [
    'task.started', 'daily.cache_hit', 'daily.fetching', 'daily.normalized',
    'task.completed', 'task.failed',
  ]
  const handle = (event: Event) => {
    const payload = JSON.parse((event as MessageEvent).data) as TaskEvent
    if (!events.value.some((item) => item.sequence === payload.sequence)) events.value.push(payload)
    if (task.value) {
      task.value.progress = payload.progress
      task.value.current_step = payload.message
    }
    void refreshTask()
    if (['task.completed', 'task.failed'].includes(payload.type)) source.close()
  }
  eventTypes.forEach((eventType) => source.addEventListener(eventType, handle))
  source.onerror = () => {
    void refreshTask().finally(() => {
      if (task.value && ['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) source.close()
    })
  }
  closeEvents = () => source.close()
}

function chooseCategory(code: string) {
  if (code === activeCode.value && task.value) {
    categoryMenuOpen.value = false
    return
  }
  void startFeed(code)
}

function openPdf(paper: ArxivDailyPaper) {
  if (!paper.pdf_url) {
    errorMessage.value = '该论文暂未提供可访问的原版 PDF。'
    return
  }
  readerPaper.value = paper
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function arxivAbstractUrl(paper: ArxivDailyPaper) {
  if (paper.arxiv_id) return `https://arxiv.org/abs/${paper.arxiv_id}`
  return paper.pdf_url.replace('/pdf/', '/abs/').replace(/\.pdf$/i, '')
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '学术速递请求失败'
}

watch(() => route.query.task, (value) => {
  closeEvents?.()
  closeEvents = null
  if (typeof value === 'string' && value) void loadTask(value)
  else task.value = null
}, { immediate: true })

onBeforeUnmount(() => closeEvents?.())
</script>

<template>
  <main v-if="readerPaper" class="daily-reader-page">
    <header class="daily-reader-topbar">
      <button type="button" @click="readerPaper = null"><ArrowLeft :size="18" />返回速递</button>
      <span><FileText :size="15" />{{ readerPaper.title }}</span>
      <a :href="pdfUrl" target="_blank" rel="noreferrer"><Download :size="15" />下载原版 PDF</a>
    </header>
    <section class="daily-reader-workspace">
      <div class="daily-reader-title"><span>{{ readerPaper.arxiv_id }}</span><strong>{{ readerPaper.title }}</strong></div>
      <iframe :src="pdfUrl" :title="`${readerPaper.title} PDF`"></iframe>
      <footer>原版 PDF · 临时缓存 · 不使用 HTML 或摘要伪造</footer>
    </section>
  </main>

  <div v-else class="daily-agent-view">
    <header class="daily-topbar">
      <div class="daily-heading">
        <span class="daily-eyebrow"><Sparkles :size="15" />智研 · 每日学术速递</span>
        <h1>{{ activeCategory.code }} · {{ activeCategory.name_cn }}</h1>
      </div>
      <div class="daily-actions">
        <button type="button" class="daily-outline-button" :disabled="busy || isRunning" @click="startFeed(activeCode, true)"><RefreshCw :size="15" :class="{ spinning: busy || isRunning }" />同步当前分类</button>
        <a href="https://www.arxivdaily.com/" target="_blank" rel="noreferrer" class="daily-source-link">源站<ExternalLink :size="14" /></a>
      </div>
    </header>

    <div class="daily-category-bar">
      <div class="daily-category-menu" :class="{ open: categoryMenuOpen }">
        <button type="button" class="daily-category-trigger" :aria-expanded="categoryMenuOpen" @click="categoryMenuOpen = !categoryMenuOpen"><Layers3 :size="17" />浏览 CS 分类<ChevronDown :size="15" /></button>
        <div v-if="categoryMenuOpen" class="daily-category-popover">
          <div><strong>计算机科学分类</strong><button type="button" aria-label="关闭分类菜单" @click="categoryMenuOpen = false"><X :size="15" /></button></div>
          <button v-for="item in categories" :key="item.code" type="button" :class="{ active: item.code === activeCode }" @click="chooseCategory(item.code)"><b>{{ item.code }}</b><span>{{ item.name_cn }}</span></button>
        </div>
      </div>
      <label class="daily-search"><Search :size="16" /><input v-model="searchQuery" type="search" placeholder="搜索标题、作者、摘要或 arXiv ID" /><span v-if="searchQuery">{{ filteredPapers.length }} / {{ papers.length }}</span></label>
    </div>

    <section class="daily-statistics">
      <div><CalendarDays :size="19" /><span><small>推送日期</small><b>{{ today }}</b></span></div>
      <div><Database :size="19" /><span><small>当前分类文章</small><b>{{ papers.length }} 篇</b></span></div>
      <div><RefreshCw :size="19" /><span><small>最近同步</small><b>{{ fetchedTime }}</b></span></div>
      <div><BookOpenCheck :size="19" /><span><small>数据状态</small><b>{{ summary.cached ? '数据库快照' : task?.status === 'SUCCEEDED' ? '源站同步' : '等待获取' }}</b></span></div>
    </section>

    <section class="daily-list-section">
      <div class="daily-list-title"><div><i></i><h2>论文列表</h2></div><span>{{ activeCategory.code }} · {{ filteredPapers.length }} 篇</span></div>
      <p v-for="warning in warnings" :key="warning" class="daily-warning">{{ warning }}</p>
      <p v-if="errorMessage" class="daily-error">{{ errorMessage }}</p>

      <div v-if="isRunning || busy" class="daily-state"><LoaderCircle class="spinning" :size="28" /><strong>正在同步最新论文</strong><span>{{ task?.current_step || '正在创建学术速递任务' }} · {{ task?.progress ?? 5 }}%</span></div>
      <div v-else-if="!task" class="daily-state"><BookOpenCheck :size="28" /><strong>选择分类并获取今日论文</strong><button type="button" @click="startFeed()">获取 {{ activeCode }} 学术速递</button></div>
      <div v-else-if="task.status === 'FAILED'" class="daily-state daily-state--error"><strong>同步失败</strong><span>{{ task.error || task.current_step }}</span><button type="button" @click="startFeed(activeCode, true)">重新同步</button></div>
      <div v-else-if="!filteredPapers.length" class="daily-state"><Search :size="26" /><strong>{{ searchQuery ? '没有匹配的论文' : '当前分类暂无论文' }}</strong><span>{{ searchQuery ? '尝试调整检索词。' : '可重新同步当前分类。' }}</span></div>

      <div v-else class="daily-paper-list">
        <article v-for="(paper, index) in filteredPapers" :key="paper.arxiv_id || `${paper.title}-${index}`" class="daily-paper-card">
          <div class="daily-paper-tags"><span>{{ paper.arxiv_id }}</span><span v-if="paper.updated">{{ paper.updated }}</span><span v-for="tag in paper.categories" :key="tag">{{ tag }}</span></div>
          <h2><span>{{ index + 1 }}</span>{{ paper.title }}</h2>
          <p class="daily-title-cn">{{ paper.title_cn }}</p>
          <p class="daily-summary"><b>AI 总结</b>{{ paper.summary_cn }}</p>
          <div class="daily-paper-links"><button type="button" @click="openPdf(paper)"><FileText :size="15" />阅读原版 PDF</button><a :href="arxivAbstractUrl(paper)" target="_blank" rel="noreferrer">arXiv 页面<ExternalLink :size="13" /></a></div>
          <dl class="daily-paper-meta">
            <div><dt><Landmark :size="15" />发表机构</dt><dd>{{ paper.affiliations?.join('；') || '源站暂未提供机构信息' }}</dd></div>
            <div><dt><UserRound :size="15" />作者</dt><dd>{{ paper.authors || '源站暂未提供作者信息' }}</dd></div>
          </dl>
          <details class="daily-details"><summary><ChevronDown :size="15" />摘要详情</summary><div><section><h3>AI 中文摘要</h3><p>{{ paper.abstract_cn }}</p></section><section><h3>英文摘要</h3><p>{{ paper.abstract }}</p></section></div></details>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.daily-agent-view,.daily-reader-page{min-height:calc(100vh - 36px);background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}.daily-topbar{min-height:90px;padding:20px 26px;display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line)}.daily-eyebrow{display:flex;align-items:center;gap:6px;color:var(--green-700);font-size:14px;font-weight:800}.daily-heading h1{margin:5px 0 0;font-size:24px}.daily-actions{display:flex;align-items:center;gap:9px}.daily-outline-button,.daily-source-link,.daily-paper-links button,.daily-paper-links a,.daily-state button{min-height:36px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);border-radius:5px;background:#fff;font-weight:700}.daily-outline-button:hover,.daily-paper-links button:hover,.daily-paper-links a:hover{border-color:var(--green-700);background:var(--green-100)}.daily-source-link{color:#fff;background:var(--green-900);border-color:var(--green-900)}.daily-category-bar{position:relative;z-index:8;padding:12px 26px;display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);background:var(--surface-soft)}.daily-category-menu{position:relative}.daily-category-trigger{min-height:38px;padding:0 12px;display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:5px;background:#fff}.daily-category-popover{position:absolute;top:46px;left:0;width:min(760px,calc(100vw - 330px));max-height:480px;padding:12px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;overflow:auto;border:1px solid var(--line-strong);border-radius:6px;background:#fff;box-shadow:var(--shadow)}.daily-category-popover>div{grid-column:1/-1;padding:3px 4px 9px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}.daily-category-popover>div button{padding:4px;display:grid;place-items:center;background:transparent}.daily-category-popover>button{min-width:0;padding:8px 9px;display:grid;gap:2px;text-align:left;border:1px solid transparent;border-radius:4px;background:var(--surface-soft)}.daily-category-popover>button:hover,.daily-category-popover>button.active{border-color:var(--green-700);background:var(--green-100)}.daily-category-popover b{font-size:14px}.daily-category-popover span{overflow:hidden;color:var(--muted);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.daily-search{width:min(460px,55%);min-height:38px;padding:0 11px;display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:5px;background:#fff}.daily-search input{min-width:0;flex:1;border:0;outline:0;background:transparent}.daily-search span{color:var(--muted);font-size:13px;white-space:nowrap}.daily-statistics{padding:0 26px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-bottom:1px solid var(--line)}.daily-statistics>div{min-height:78px;padding:15px 16px;display:flex;align-items:center;gap:11px;border-right:1px solid var(--line)}.daily-statistics>div:last-child{border-right:0}.daily-statistics svg{color:var(--green-700)}.daily-statistics small,.daily-statistics b{display:block}.daily-statistics small{color:var(--muted);font-size:12px}.daily-statistics b{margin-top:2px;font-size:15px}.daily-list-section{padding:22px 26px 38px}.daily-list-title{margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}.daily-list-title>div{display:flex;align-items:center;gap:8px}.daily-list-title i{width:7px;height:7px;background:var(--acid);border:2px solid var(--green-900);border-radius:50%}.daily-list-title h2{margin:0;font-size:17px}.daily-list-title>span{padding:4px 8px;color:var(--muted);background:var(--green-100);border-radius:4px;font-size:13px}.daily-paper-list{display:grid;gap:12px}.daily-paper-card{padding:20px 22px;border:1px solid var(--line);border-radius:7px;background:#fff}.daily-paper-card:hover{border-color:var(--line-strong);box-shadow:0 5px 18px rgb(23 33 28 / 6%)}.daily-paper-tags{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.daily-paper-tags span{padding:2px 6px;color:var(--muted);background:var(--surface-soft);border:1px solid var(--line);border-radius:3px;font-size:12px}.daily-paper-card h2{margin:10px 0 5px;display:flex;align-items:flex-start;gap:10px;font-size:17px;line-height:1.5}.daily-paper-card h2>span{width:25px;height:25px;flex:0 0 auto;display:grid;place-items:center;color:#fff;background:var(--green-900);border-radius:4px;font-size:13px}.daily-title-cn{margin:0;color:var(--green-700);font-weight:700}.daily-summary{margin:12px 0;padding:11px 13px;color:var(--muted);background:var(--green-100);border-left:3px solid var(--green-700);line-height:1.7}.daily-summary b{margin-right:8px;color:var(--ink)}.daily-paper-links{display:flex;gap:8px}.daily-paper-links button,.daily-paper-links a{min-height:32px;padding:0 10px;font-size:14px}.daily-paper-meta{margin:15px 0 0;padding-top:13px;display:grid;grid-template-columns:1fr 1fr;gap:11px 20px;border-top:1px solid var(--line)}.daily-paper-meta div{min-width:0}.daily-paper-meta dt{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:13px}.daily-paper-meta dd{margin:4px 0 0;line-height:1.6}.daily-details{margin-top:13px;border-top:1px solid var(--line)}.daily-details summary{padding:12px 0 0;display:flex;align-items:center;gap:5px;color:var(--green-700);font-weight:700;cursor:pointer}.daily-details[open] summary svg{transform:rotate(180deg)}.daily-details>div{padding-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:18px}.daily-details h3{margin:0 0 6px;font-size:15px}.daily-details p{margin:0;color:var(--muted);line-height:1.75}.daily-state{min-height:360px;display:grid;place-items:center;align-content:center;gap:8px;color:var(--muted);text-align:center}.daily-state button{margin-top:8px;color:#fff;background:var(--green-900);border-color:var(--green-900)}.daily-warning,.daily-error{padding:9px 11px;border-left:3px solid var(--warning);background:#fff9ed}.daily-error{color:var(--danger);border-left-color:var(--danger);background:#fff4f2}.daily-reader-page{display:grid;grid-template-rows:auto minmax(0,1fr)}.daily-reader-topbar{min-height:60px;padding:10px 18px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;border-bottom:1px solid var(--line)}.daily-reader-topbar button,.daily-reader-topbar a{display:flex;align-items:center;gap:6px;background:transparent;font-weight:700}.daily-reader-topbar>span{overflow:hidden;display:flex;align-items:center;gap:7px;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.daily-reader-workspace{min-height:0;padding:16px;background:var(--surface-soft)}.daily-reader-title{padding:10px 14px;display:flex;align-items:center;gap:10px;border:1px solid var(--line);border-bottom:0;background:#fff}.daily-reader-title span{padding:3px 6px;background:var(--green-100);border-radius:3px;font-size:13px}.daily-reader-title strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.daily-reader-workspace iframe{width:100%;height:calc(100vh - 180px);display:block;border:1px solid var(--line);background:#fff}.daily-reader-workspace footer{padding:8px 12px;color:var(--muted);border:1px solid var(--line);border-top:0;background:#fff;font-size:13px}.spinning{animation:daily-spin .9s linear infinite}@keyframes daily-spin{to{transform:rotate(360deg)}}
@media(max-width:1000px){.daily-category-popover{grid-template-columns:repeat(3,minmax(0,1fr));width:min(620px,calc(100vw - 280px))}.daily-statistics{grid-template-columns:1fr 1fr}.daily-statistics>div:nth-child(2){border-right:0}.daily-statistics>div:nth-child(-n+2){border-bottom:1px solid var(--line)}}
@media(max-width:720px){.daily-agent-view,.daily-reader-page{border-left:0;border-right:0;border-radius:0}.daily-topbar{padding:16px;align-items:flex-start}.daily-heading h1{font-size:20px}.daily-actions{align-items:stretch;flex-direction:column}.daily-source-link{min-height:34px}.daily-category-bar{padding:10px 16px;align-items:stretch;flex-direction:column}.daily-search{width:100%}.daily-category-popover{position:fixed;inset:76px 12px auto;width:auto;max-height:70vh;grid-template-columns:1fr 1fr}.daily-statistics,.daily-paper-meta,.daily-details>div{grid-template-columns:1fr}.daily-statistics>div{min-height:64px;border-right:0;border-bottom:1px solid var(--line)}.daily-list-section{padding:18px 14px 30px}.daily-paper-card{padding:16px}.daily-paper-card h2{font-size:16px}.daily-paper-links{align-items:stretch;flex-direction:column}.daily-reader-topbar{grid-template-columns:auto minmax(0,1fr)}.daily-reader-topbar a{grid-column:1/-1}.daily-reader-workspace{padding:8px}.daily-reader-workspace iframe{height:calc(100vh - 210px)}}
</style>
