<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowUp,
  Bot,
  CheckCircle2,
  FileCheck2,
  FileText,
  LoaderCircle,
  PenLine,
  Rows3,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { ManuscriptSection, ResearchTask } from '@/types'
import { renderMarkdown } from '@/utils/renderMarkdown'

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
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'manuscript' | 'sections' | 'checks'>('manuscript')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const sections = computed<ManuscriptSection[]>(() => output.value.sections ?? [])
const plan = computed(() => output.value.manuscript_plan)
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const manuscriptHtml = computed(() => renderMarkdown(output.value.manuscript_markdown ?? ''))

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
      agent_code: 'manuscript_assistance',
      model: agentPayload?.model ?? 'vertical_domain',
      attachment: agentPayload?.attachment ?? null,
      link: agentPayload?.link ?? null,
      model_config_id: agentPayload?.model.startsWith('model_config:')
        ? agentPayload.model.slice('model_config:'.length)
        : null,
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
    'manuscript.plan_ready',
    'manuscript.sections_started',
    'manuscript.sections_ready',
    'manuscript.quality_checked',
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

function resetWorkspace() {
  closeEvents?.()
  closeEvents = null
  task.value = null
  events.value = []
  query.value = ''
  errorMessage.value = ''
  void router.replace({ path: route.path })
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
        <span class="literature-agent-mark"><PenLine :size="18" /></span>
        <span><strong>文稿辅助</strong><small>Manuscript Assistance Agent · 论文写作与润色工作流</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建文稿</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FileText :size="25" /></span>
        <p class="eyebrow">MANUSCRIPT ASSISTANCE AGENT</p>
        <h1>从写作需求生成结构化科研文稿</h1>
        <p>适合生成摘要、引言、相关工作、方法、实验方案和总结，也可以把创新点整理成论文写作素材。</p>
      </div>
      <AgentPromptBox
        v-model="query"
        :busy="busy"
        placeholder="例如：帮我写一篇关于动态 RAG 安全评估方法的论文初稿"
        hint="章节规划 · 内容生成 · 质量检查 · Markdown 文稿"
        @submit="startTask"
      />
      <div v-if="false" class="literature-query-box">
        <textarea v-model="query" rows="4" placeholder="例如：帮我写一篇关于动态 RAG 安全评估方法的论文初稿" @keydown.ctrl.enter.prevent="startTask"></textarea>
        <div>
          <span>章节规划 · 内容生成 · 质量检查 · Markdown 文稿</span>
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
          <span>写作任务</span>
          <strong>{{ task.progress }}%</strong>
          <h1>{{ task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div v-if="plan" class="literature-plan manuscript-plan-card">
          <div class="literature-section-label"><Rows3 :size="15" />写作规划</div>
          <p>{{ plan.topic }}</p>
          <div class="literature-keywords">
            <span v-for="keyword in plan.keywords" :key="keyword">{{ keyword }}</span>
            <span>{{ plan.language }}</span>
          </div>
          <ol class="agent-step-list">
            <li v-for="section in plan.sections" :key="section.id"><i></i><span>{{ section.title }}</span></li>
          </ol>
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
        <nav class="literature-result-tabs" aria-label="文稿结果视图">
          <button type="button" :class="{ active: activeTab === 'manuscript' }" @click="activeTab = 'manuscript'"><FileText :size="15" />完整文稿</button>
          <button type="button" :class="{ active: activeTab === 'sections' }" @click="activeTab = 'sections'"><Rows3 :size="15" />章节拆分<span>{{ sections.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'checks' }" @click="activeTab = 'checks'"><FileCheck2 :size="15" />质量检查</button>
        </nav>

        <article v-if="activeTab === 'manuscript'" class="literature-report-view">
          <p v-for="warning in output.manuscript_warnings ?? []" :key="warning" class="literature-error">{{ warning }}</p>
          <div v-if="!output.manuscript_markdown" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <FileText v-else :size="24" />
            <strong>{{ isRunning ? '正在生成文稿' : '文稿尚未生成' }}</strong>
            <span>{{ task.current_step }}</span>
          </div>
          <div v-else class="markdown-document" v-html="manuscriptHtml"></div>
        </article>

        <div v-else-if="activeTab === 'sections'" class="agent-card-list">
          <article v-for="section in sections" :key="section.id" class="agent-result-card">
            <span class="status-tag">{{ section.id }}</span>
            <h2>{{ section.title }}</h2>
            <div class="markdown-document markdown-document--section" v-html="renderMarkdown(section.content)"></div>
          </article>
          <div v-if="sections.length === 0" class="literature-result-empty">
            <Rows3 :size="24" />
            <strong>章节拆分尚未完成</strong>
          </div>
        </div>

        <div v-else class="agent-card-list">
          <article class="agent-result-card">
            <span class="status-tag">CHECKS</span>
            <h2>质量检查</h2>
            <p v-for="item in plan?.checks ?? ['结构完整性', '章节一致性', '学术表达', 'Markdown 格式']" :key="item">
              <CheckCircle2 :size="15" />{{ item }}
            </p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">METRICS</span>
            <h2>生成指标</h2>
            <p>章节数：{{ output.metrics?.section_count ?? sections.length }}</p>
            <p>字符数：{{ output.metrics?.character_count ?? 0 }}</p>
            <p>行数：{{ output.metrics?.line_count ?? 0 }}</p>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>
