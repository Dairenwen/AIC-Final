<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowUp,
  Bot,
  CheckCircle2,
  ExternalLink,
  FlaskConical,
  Lightbulb,
  LoaderCircle,
  Network,
  Route,
  Search,
  Sparkles,
} from 'lucide-vue-next'

import { apiUrl, http, taskRecordFromResponse } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { InnovationProposal, ResearchTask } from '@/types'

interface TaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
}

interface AgentPromptPayload {
  prompt: string
  model: string
  attachment: string | null
  link: string | null
}

const route = useRoute()
const router = useRouter()
const query = ref('')
const innovationMode = ref<'full' | 'expand' | 'evaluate'>('full')
const topK = ref(5)
const timeRange = ref('2022-2026')
const seedIdeas = ref('')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'ideas' | 'signals' | 'evidence'>('ideas')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const proposals = computed<InnovationProposal[]>(() => output.value.innovations ?? [])
const trends = computed(() => output.value.research_trends ?? [])
const gaps = computed(() => output.value.research_gaps ?? [])
const corpus = computed(() => output.value.literature_corpus ?? [])
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))

async function startTask(payload?: AgentPromptPayload | Event) {
  const agentPayload = isAgentPromptPayload(payload) ? payload : null
  const prompt = agentPayload?.prompt ?? query.value.trim()
  if (!prompt || busy.value) return
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const response = await http.post('/tasks', {
      prompt,
      agent_code: 'innovation_point_generation',
      model: agentPayload?.model ?? 'vertical_domain',
      attachment: agentPayload?.attachment ?? null,
      link: agentPayload?.link ?? null,
      innovation_mode: innovationMode.value,
      innovation_top_k: topK.value,
      innovation_time_range: timeRange.value.trim() || null,
      innovation_seed_ideas: seedIdeas.value.split(/[，,;；\n]+/).map((item) => item.trim()).filter(Boolean),
    })
    const createdTask = normalizeTaskResponse(response.data)
    task.value = createdTask
    await router.replace({ path: route.path, query: { task: createdTask.id } })
    subscribe(createdTask.id)
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
    task.value = normalizeTaskResponse(response.data)
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
  task.value = normalizeTaskResponse(response.data)
}

function subscribe(taskId: string) {
  if (!taskId.trim()) {
    errorMessage.value = '任务创建接口未返回有效的任务标识'
    return
  }
  closeEvents?.()
  const source = new EventSource(apiUrl(`/tasks/${taskId}/events`))
  const eventTypes = [
    'task.started',
    'innovation.domain_ready',
    'innovation.corpus_ready',
    'innovation.workflow_started',
    'innovation.trends_ready',
    'innovation.gaps_ready',
    'innovation.proposals_ready',
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

/**
 * Keep the innovation view compatible with deployments that still return the
 * legacy task envelope (`task_id`/`taskId`, or a nested `task` object).  The
 * current API returns `{ success: true, data: { id: ... } }`, but blindly
 * reading `response.data.data.id` turns a contract mismatch into requests to
 * `/tasks/undefined` and hides the actual server response.
 */
function normalizeTaskResponse(value: unknown): ResearchTask {
  const raw = taskRecordFromResponse(value)
  const id = raw.id ?? raw.task_id ?? raw.taskId
  if (id == null || String(id).trim() === '') {
    throw new Error('任务创建接口未返回有效的任务标识，请检查服务器 API 版本')
  }
  if (raw.task_type && raw.task_type !== 'INNOVATION_POINT_GENERATION') {
    throw new Error('当前任务不是创新点生成任务，请从创新点生成页面重新提交')
  }
  return { ...raw, id: String(id), output: isRecord(raw.output) ? raw.output : {} } as unknown as ResearchTask
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
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

function textOf(item: Record<string, unknown>, key: string, fallback = '') {
  const value = item[key]
  if (value == null) return fallback
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function scoreText(value?: number) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

function sourceUrl(item: Record<string, unknown>) {
  const value = String(item.source_url || item.pdf_url || '')
  return /^https?:\/\//i.test(value) ? value : ''
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
        <span class="literature-agent-mark"><Lightbulb :size="18" /></span>
        <span><strong>创新点生成</strong><small>Innovation Point Generation Agent · 趋势、空白、方案与证据链</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建生成</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><Sparkles :size="25" /></span>
        <p class="eyebrow">INNOVATION POINT GENERATION AGENT</p>
        <h1>从研究方向挖掘可验证的创新点</h1>
        <p>工作流会读取本地文献库，完成趋势分析、研究空白识别、创新点生成、评分评估和证据绑定。</p>
      </div>
      <div class="innovation-run-controls">
        <div class="innovation-mode-control">
          <span>生成模式</span>
          <div class="segment-control" role="tablist" aria-label="创新点生成模式">
            <button type="button" :class="{ active: innovationMode === 'full' }" @click="innovationMode = 'full'">完整挖掘</button>
            <button type="button" :class="{ active: innovationMode === 'expand' }" @click="innovationMode = 'expand'">种子扩展</button>
            <button type="button" :class="{ active: innovationMode === 'evaluate' }" @click="innovationMode = 'evaluate'">种子评估</button>
          </div>
        </div>
        <label><span>创新点数量</span><input v-model.number="topK" type="number" min="1" max="10" /></label>
        <label><span>文献年份</span><input v-model="timeRange" type="text" placeholder="2022-2026" /></label>
        <label class="innovation-seed-field"><span>种子想法</span><input v-model="seedIdeas" type="text" placeholder="多个想法用逗号分隔" /></label>
      </div>
      <AgentPromptBox
        v-model="query"
        :busy="busy"
        placeholder="例如：围绕动态 RAG 的可靠性评估生成 5 个创新点"
        hint="本地文献语料 · 趋势分析 · 空白识别 · 创新评分"
        :show-model-selector="false"
        @submit="startTask"
      />
      <div v-if="false" class="literature-query-box">
        <textarea v-model="query" rows="4" placeholder="例如：围绕动态 RAG 的可靠性评估生成 5 个创新点" @keydown.ctrl.enter.prevent="startTask"></textarea>
        <div>
          <span>本地文献语料 · 趋势分析 · 空白识别 · 创新评分</span>
          <button type="button" :disabled="!query.trim() || busy" title="开始生成" @click="startTask">
            <LoaderCircle v-if="busy" class="spin" :size="18" />
            <ArrowUp v-else :size="18" />
          </button>
        </div>
      </div>
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading">
          <span>创新挖掘任务</span>
          <strong>{{ task.progress }}%</strong>
          <h1>{{ task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="literature-plan manuscript-plan-card">
          <div class="literature-section-label"><Search :size="15" />研究输入</div>
          <p>{{ output.request_plan?.domain || task.prompt }}</p>
          <div class="literature-keywords">
            <span v-for="keyword in output.request_plan?.keywords ?? []" :key="keyword">{{ keyword }}</span>
            <span>Top {{ output.request_plan?.top_k ?? 5 }}</span>
            <span>{{ output.request_plan?.mode === 'evaluate' ? '种子评估' : output.request_plan?.mode === 'expand' ? '种子扩展' : '完整挖掘' }}</span>
            <span v-if="output.request_plan?.time_range">{{ output.request_plan.time_range }}</span>
          </div>
          <div class="agent-mini-metrics">
            <span><Network :size="14" />趋势 {{ trends.length }}</span>
            <span><Route :size="14" />空白 {{ gaps.length }}</span>
            <span><Lightbulb :size="14" />方案 {{ proposals.length }}</span>
          </div>
        </div>

        <div class="literature-event-log manuscript-event-log">
          <div class="literature-section-label"><Bot :size="15" />Agent 进度</div>
          <ol>
            <li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li>
            <li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li>
          </ol>
        </div>
      </section>

      <section class="literature-result-pane">
        <nav class="literature-result-tabs" aria-label="创新点结果视图">
          <button type="button" :class="{ active: activeTab === 'ideas' }" @click="activeTab = 'ideas'"><Lightbulb :size="15" />创新点<span>{{ proposals.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'signals' }" @click="activeTab = 'signals'"><Network :size="15" />趋势与空白</button>
          <button type="button" :class="{ active: activeTab === 'evidence' }" @click="activeTab = 'evidence'"><FlaskConical :size="15" />证据链</button>
        </nav>

        <div v-if="activeTab === 'ideas'" class="agent-card-list">
          <article v-for="proposal in proposals" :key="proposal.innovation_id || proposal.title" class="agent-result-card innovation-card">
            <span class="literature-paper-rank">{{ proposal.rank ?? '-' }}</span>
            <div>
              <p class="status-tag">{{ proposal.method_type || '创新方案' }}</p>
              <h2>{{ proposal.title }}</h2>
              <p>{{ proposal.summary || proposal.problem }}</p>
              <div class="innovation-score-grid">
                <span>综合 {{ scoreText(proposal.overall_score) }}</span>
                <span>新颖 {{ scoreText(proposal.scores?.novelty) }}</span>
                <span>可行 {{ scoreText(proposal.scores?.feasibility) }}</span>
                <span>影响 {{ scoreText(proposal.scores?.impact) }}</span>
                <span>风险 {{ scoreText(proposal.scores?.risk) }}</span>
              </div>
              <p v-if="proposal.research_question"><strong>研究问题：</strong>{{ proposal.research_question }}</p>
              <p v-if="proposal.method_route"><strong>方法路线：</strong>{{ proposal.method_route }}</p>
              <p v-if="proposal.validation_plan"><strong>验证方案：</strong>{{ proposal.validation_plan }}</p>
            </div>
          </article>
          <div v-if="proposals.length === 0" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <Lightbulb v-else :size="24" />
            <strong>{{ isRunning ? '正在生成创新点方案' : '尚未生成创新点' }}</strong>
            <span>{{ task.current_step }}</span>
          </div>
        </div>

        <div v-else-if="activeTab === 'signals'" class="agent-signal-grid">
          <section>
            <h2>研究趋势</h2>
            <article v-for="trend in trends" :key="textOf(trend, 'id', textOf(trend, 'name'))" class="agent-result-card">
              <span class="status-tag">{{ textOf(trend, 'id', 'TREND') }}</span>
              <h3>{{ textOf(trend, 'name', '未命名趋势') }}</h3>
              <p>{{ textOf(trend, 'signal') }}</p>
            </article>
          </section>
          <section>
            <h2>研究空白</h2>
            <article v-for="gap in gaps" :key="textOf(gap, 'id', textOf(gap, 'title'))" class="agent-result-card">
              <span class="status-tag">{{ textOf(gap, 'gap_type', 'GAP') }}</span>
              <h3>{{ textOf(gap, 'title', '未命名空白') }}</h3>
              <p>{{ textOf(gap, 'description') }}</p>
            </article>
          </section>
        </div>

        <div v-else class="agent-card-list">
          <article v-for="proposal in proposals" :key="`evidence-${proposal.innovation_id || proposal.title}`" class="agent-result-card innovation-evidence-card">
            <span class="status-tag">{{ proposal.evidence?.length || 0 }} 条证据</span>
            <h2>{{ proposal.title }}</h2>
            <div v-if="proposal.evidence?.length" class="innovation-evidence-list">
              <div v-for="evidenceItem in proposal.evidence" :key="textOf(evidenceItem, 'id', textOf(evidenceItem, 'title'))">
                <strong>{{ textOf(evidenceItem, 'title', '未命名文献') }}</strong>
                <small>{{ textOf(evidenceItem, 'year') }} {{ textOf(evidenceItem, 'venue') }}</small>
                <p>{{ textOf(evidenceItem, 'snippet') }}</p>
                <a v-if="sourceUrl(evidenceItem)" :href="sourceUrl(evidenceItem)" target="_blank" rel="noopener noreferrer">查看来源<ExternalLink :size="13" /></a>
              </div>
            </div>
            <p v-else>当前语料中没有可绑定到该方案的直接文献证据。</p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">KNOWLEDGE</span>
            <h2>知识图谱摘要</h2>
            <p>{{ output.knowledge_graph_summary || '暂无摘要' }}</p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">CITATION</span>
            <h2>引用网络摘要</h2>
            <p>{{ output.citation_network_summary || '暂无摘要' }}</p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">CORPUS</span>
            <h2>语料证据</h2>
            <p><CheckCircle2 :size="15" />已纳入 {{ corpus.length }} 条候选文献证据</p>
            <p>后续写作可优先使用创新点卡片中的 downstream_wengao_inputs 字段。</p>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>
