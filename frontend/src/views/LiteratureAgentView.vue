<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowUp,
  Bot,
  CheckCircle2,
  ExternalLink,
  FileSearch,
  FileText,
  ListTree,
  LoaderCircle,
  Route as RouteIcon,
  Search,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { LiteraturePaper, ResearchTask } from '@/types'

interface TaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
  source?: string
}

interface AgentPromptPayload {
  prompt: string
  model: string
  attachment: string | null
  link: string | null
}

type ResizePanel = 'plan' | 'sources' | 'events'

const route = useRoute()
const router = useRouter()
const query = ref('')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'papers' | 'report' | 'timeline'>('papers')
const panelHeights = ref<Record<ResizePanel, number>>({
  // Keep the three trace sections close to the balanced proportions used by
  // the desktop literature-search layout. The previous event-heavy defaults
  // forced the query plan and source cards down to their minimum heights.
  plan: 230,
  sources: 155,
  events: 245,
})
let closeEvents: (() => void) | null = null

const minPanelHeights: Record<ResizePanel, number> = {
  plan: 82,
  sources: 92,
  events: 140,
}

const output = computed(() => task.value?.output ?? {})
const papers = computed(() => output.value.papers ?? [])
const queryPlan = computed(() => output.value.query_plan)
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const reportBlocks = computed(() => parseMarkdown(output.value.report_markdown ?? ''))

async function startSearch(payload?: AgentPromptPayload | Event) {
  const agentPayload = isAgentPromptPayload(payload) ? payload : null
  const prompt = agentPayload?.prompt ?? query.value.trim()
  if (!prompt || busy.value) return
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const response = await http.post('/tasks', {
      prompt,
      agent_code: 'literature_search',
      model: agentPayload?.model ?? 'auto',
      attachment: agentPayload?.attachment ?? null,
      link: agentPayload?.link ?? null,
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

function isAgentPromptPayload(value: AgentPromptPayload | Event | undefined): value is AgentPromptPayload {
  return Boolean(value && 'prompt' in value && 'model' in value && 'attachment' in value)
}

async function loadTask(taskId: string) {
  errorMessage.value = ''
  try {
    const response = await http.get(`/tasks/${taskId}`)
    task.value = response.data.data as ResearchTask
    query.value = task.value.prompt
    subscribe(taskId)
  } catch (error) {
    task.value = null
    errorMessage.value = requestError(error)
  }
}

async function refreshTask() {
  if (!task.value) return
  const response = await http.get(`/tasks/${task.value.id}`)
  task.value = response.data.data as ResearchTask
}

function subscribe(taskId: string) {
  closeEvents?.()
  const source = new EventSource(apiUrl(`/tasks/${taskId}/events`))
  const eventTypes = [
    'task.started',
    'query.rewritten',
    'source.completed',
    'papers.ranked',
    'report.ready',
    'literature.list_ready',
    'timeline.inserted',
    'task.completed',
    'task.failed',
  ]
  const handle = (event: Event) => {
    const payload = JSON.parse((event as MessageEvent).data) as TaskEvent
    if (!events.value.some((item) => item.sequence === payload.sequence)) events.value.push(payload)
    if (task.value) {
      task.value.progress = payload.progress
      task.value.current_step = payload.message
    }
    if (payload.type !== 'timeline.inserted' || payload.progress % 4 === 0) void refreshTask()
    if (['task.completed', 'task.failed'].includes(payload.type)) {
      void refreshTask()
      source.close()
    }
  }
  eventTypes.forEach((eventType) => source.addEventListener(eventType, handle))
  source.onerror = () => {
    void refreshTask().finally(() => {
      if (task.value && ['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) source.close()
    })
  }
  closeEvents = () => source.close()
}

function resetWorkspace() {
  closeEvents?.()
  closeEvents = null
  task.value = null
  events.value = []
  query.value = ''
  errorMessage.value = ''
  void router.replace({ path: route.path })
}

function panelStyle(panel: ResizePanel) {
  return { '--panel-size': `${panelHeights.value[panel]}px` }
}

function startPanelResize(upper: ResizePanel, lower: ResizePanel, event: PointerEvent) {
  event.preventDefault()
  const startY = event.clientY
  const startUpper = panelHeights.value[upper]
  const startLower = panelHeights.value[lower]
  const total = startUpper + startLower

  function onPointerMove(moveEvent: PointerEvent) {
    const nextUpper = clamp(
      startUpper + moveEvent.clientY - startY,
      minPanelHeights[upper],
      total - minPanelHeights[lower],
    )
    panelHeights.value = {
      ...panelHeights.value,
      [upper]: nextUpper,
      [lower]: total - nextUpper,
    }
  }

  function onPointerUp() {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    document.body.classList.remove('is-resizing-literature-panels')
  }

  document.body.classList.add('is-resizing-literature-panels')
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp, { once: true })
}

function resizePanelsWithKeyboard(upper: ResizePanel, lower: ResizePanel, event: KeyboardEvent) {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return
  event.preventDefault()
  const delta = event.key === 'ArrowUp' ? -18 : 18
  const total = panelHeights.value[upper] + panelHeights.value[lower]
  const nextUpper = clamp(panelHeights.value[upper] + delta, minPanelHeights[upper], total - minPanelHeights[lower])
  panelHeights.value = {
    ...panelHeights.value,
    [upper]: nextUpper,
    [lower]: total - nextUpper,
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    local_knowledge: '本地文献库',
    personal_knowledge: '个人收藏',
    google_scholar: 'Google Scholar',
    arxiv: 'arXiv',
  }
  return labels[source] ?? source
}

function paperLink(paper: LiteraturePaper) {
  return paper.pdf_url || paper.url || ''
}

function parseMarkdown(markdown: string) {
  return markdown.split('\n').map((line) => {
    const text = line.trim().replaceAll('**', '')
    if (text.startsWith('### ')) return { type: 'h3', text: text.slice(4) }
    if (text.startsWith('## ')) return { type: 'h2', text: text.slice(3) }
    if (text.startsWith('# ')) return { type: 'h1', text: text.slice(2) }
    if (text.startsWith('- ')) return { type: 'li', text: text.slice(2) }
    return { type: text ? 'p' : 'space', text }
  })
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '请求失败'
}

watch(
  () => route.query.task,
  (value) => {
    closeEvents?.()
    closeEvents = null
    if (typeof value === 'string' && value) void loadTask(value)
    else task.value = null
  },
  { immediate: true },
)

onBeforeUnmount(() => closeEvents?.())
</script>

<template>
  <div class="literature-agent-view">
    <header class="literature-agent-header">
      <div>
        <span class="literature-agent-mark"><Bot :size="18" /></span>
        <span><strong>文献检索</strong><small>Literature Search Agent · LangGraph 六阶段工作流</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建检索</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FileSearch :size="25" /></span>
        <p class="eyebrow">LITERATURE SEARCH AGENT</p>
        <h1>从研究问题建立可追溯的文献脉络</h1>
        <p>工作流会完成查询改写、数据库与学术源检索、去重排序、研究报告和年度脉络图生成。</p>
      </div>
      <AgentPromptBox
        v-model="query"
        :busy="busy"
        placeholder="例如：检索近 3 年动态 RAG 的代表性文献"
        hint="数据库文献库 · 个人收藏 · Google Scholar · arXiv"
        @submit="startSearch"
      />
      <div v-if="false" class="literature-query-box">
        <textarea v-model="query" rows="4" placeholder="例如：检索近 3 年动态 RAG 的代表性文献" @keydown.ctrl.enter.prevent="startSearch"></textarea>
        <div><span>数据库文献库 · 个人收藏 · Google Scholar · arXiv</span><button type="button" :disabled="!query.trim() || busy" title="开始检索" @click="startSearch"><LoaderCircle v-if="busy" class="spin" :size="18" /><ArrowUp v-else :size="18" /></button></div>
      </div>
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading">
          <span>研究任务</span>
          <strong>{{ task.progress }}%</strong>
          <h1>{{ task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="literature-panel-stack">
        <div v-if="queryPlan" class="literature-plan literature-resizable-panel" :style="panelStyle('plan')">
          <div class="literature-section-label"><Search :size="15" />查询改写</div>
          <p>{{ queryPlan.intent_summary }}</p>
          <div class="literature-keywords"><span v-for="keyword in queryPlan.keywords" :key="keyword">{{ keyword }}</span><span>{{ queryPlan.start_year }}–{{ queryPlan.end_year }}</span></div>
          <details><summary>查看 5 条检索式</summary><ol><li v-for="item in queryPlan.queries" :key="item">{{ item }}</li></ol></details>
        </div>

          <div
            v-if="queryPlan"
            class="literature-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            tabindex="0"
            title="拖动调整上下区域高度"
            @pointerdown="startPanelResize('plan', 'sources', $event)"
            @keydown="resizePanelsWithKeyboard('plan', 'sources', $event)"
          ></div>

        <div class="literature-sources literature-resizable-panel" :style="panelStyle('sources')">
          <div class="literature-section-label"><ListTree :size="15" />并行检索</div>
          <div class="literature-source-grid">
            <div v-for="source in ['local_knowledge', 'personal_knowledge', 'google_scholar', 'arxiv']" :key="source">
              <span><CheckCircle2 v-if="output.source_progress?.[source]?.status === 'completed'" :size="14" /><LoaderCircle v-else-if="isRunning" class="spin" :size="14" /><Search v-else :size="14" />{{ sourceLabel(source) }}</span>
              <strong>{{ output.source_progress?.[source]?.count ?? 0 }} 篇</strong>
            </div>
          </div>
        </div>

          <div
            class="literature-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            tabindex="0"
            title="拖动调整上下区域高度"
            @pointerdown="startPanelResize('sources', 'events', $event)"
            @keydown="resizePanelsWithKeyboard('sources', 'events', $event)"
          ></div>

        <div class="literature-event-log literature-resizable-panel" :style="panelStyle('events')">
          <div class="literature-section-label"><Bot :size="15" />Agent 进度</div>
          <ol>
            <li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li>
            <li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li>
          </ol>
        </div>

        </div>

        <div class="literature-compact-composer">
          <textarea v-model="query" rows="2" :disabled="isRunning" placeholder="输入新的研究问题" @keydown.ctrl.enter.prevent="startSearch"></textarea>
          <button type="button" :disabled="!query.trim() || isRunning" title="开始新的检索" @click="startSearch"><ArrowUp :size="17" /></button>
        </div>
      </section>

      <section class="literature-result-pane">
        <nav class="literature-result-tabs" aria-label="检索结果视图">
          <button type="button" :class="{ active: activeTab === 'papers' }" @click="activeTab = 'papers'"><FileSearch :size="15" />检索结果 <span>{{ papers.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'report' }" @click="activeTab = 'report'"><FileText :size="15" />研究报告</button>
          <button type="button" :class="{ active: activeTab === 'timeline' }" @click="activeTab = 'timeline'"><RouteIcon :size="15" />年度脉络</button>
        </nav>

        <div v-if="activeTab === 'papers'" class="literature-paper-list">
          <div v-if="papers.length === 0" class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><FileSearch v-else :size="24" /><strong>{{ isRunning ? '正在汇总与排序文献' : '未检索到满足条件的文献' }}</strong><span>{{ task.current_step }}</span></div>
          <article v-for="(paper, index) in papers" :key="paper.id || `${paper.title}-${index}`">
            <span class="literature-paper-rank">{{ index + 1 }}</span>
            <div>
              <p>{{ paper.source }} · {{ paper.year || '年份未知' }} · {{ paper.citation_count ?? 0 }} Cite</p>
              <h2>{{ paper.title }}</h2>
              <small>{{ paper.authors.join(' · ') || '作者信息缺失' }}</small>
              <p class="literature-paper-abstract">{{ paper.abstract }}</p>
              <span class="literature-paper-venue">{{ paper.venue || '来源未知' }}</span>
            </div>
            <a v-if="paperLink(paper)" :href="paperLink(paper)" target="_blank" rel="noreferrer" title="打开论文"><ExternalLink :size="16" /></a>
          </article>
        </div>

        <article v-else-if="activeTab === 'report'" class="literature-report-view">
          <div v-if="!output.report_markdown" class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><FileText v-else :size="24" /><strong>研究报告尚未生成</strong></div>
          <template v-for="(block, index) in reportBlocks" :key="index">
            <h1 v-if="block.type === 'h1'">{{ block.text }}</h1>
            <h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2>
            <h3 v-else-if="block.type === 'h3'">{{ block.text }}</h3>
            <li v-else-if="block.type === 'li'">{{ block.text }}</li>
            <p v-else-if="block.type === 'p'">{{ block.text }}</p>
            <br v-else />
          </template>
        </article>

        <div v-else class="literature-timeline-view">
          <img v-if="output.fishbone_url" :src="output.fishbone_url" alt="年度文献发表脉络" />
          <div v-else class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><RouteIcon v-else :size="24" /><strong>年度脉络图尚未生成</strong><span>文献排序完成后将逐篇插入脉络图</span></div>
        </div>
      </section>
    </div>
  </div>
</template>
