<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bot, CalendarClock, CheckCircle2, ClipboardList, LoaderCircle, Send, Target, Trophy } from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { ResearchTask, SubmissionRecommendation } from '@/types'

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
  file: File | null
}

const route = useRoute()
const router = useRouter()
const query = ref('')
const targetLevels = ref('CCF-A,CCF-B')
const noveltyLevel = ref<'incremental' | 'substantial' | 'breakthrough'>('substantial')
const experimentCompleteness = ref(0.72)
const maxReviewWeeks = ref(12)
const preferOA = ref(true)
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'venues' | 'strategy' | 'report'>('venues')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const recommendations = computed<SubmissionRecommendation[]>(() => output.value.recommendations ?? [])
const checklist = computed(() => output.value.submission_checklist ?? {})
const timeline = computed(() => output.value.submission_strategy?.timeline ?? [])
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const reportBlocks = computed(() => parseMarkdown(output.value.final_report ?? ''))

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
      agent_code: 'contribution_recommendation',
      model: agentPayload?.model ?? 'vertical_domain',
      attachment: agentPayload?.attachment ?? null,
      link: agentPayload?.link ?? null,
      contribution_target_levels: targetLevels.value.split(/[,，、\s]+/).filter(Boolean),
      contribution_novelty_level: noveltyLevel.value,
      contribution_experiment_completeness: experimentCompleteness.value,
      contribution_max_review_weeks: maxReviewWeeks.value,
      contribution_prefer_oa: preferOA.value,
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
    'submission.features_ready',
    'submission.candidates_ready',
    'submission.ranking_ready',
    'submission.report_ready',
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

function tierLabel(value?: string) {
  if (value === 'sprint') return '冲刺'
  if (value === 'match') return '匹配'
  if (value === 'safety') return '保底'
  return value || '推荐'
}

function scoreText(value?: number) {
  return typeof value === 'number' ? value.toFixed(2) : '-'
}

function parseMarkdown(markdown: string) {
  return markdown.split('\n').map((line) => {
    const text = line.trim().replaceAll('**', '')
    if (text.startsWith('## ')) return { type: 'h2', text: text.slice(3) }
    if (text.startsWith('# ')) return { type: 'h1', text: text.slice(2) }
    if (/^\d+\.\s/.test(text)) return { type: 'li', text }
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
        <span class="literature-agent-mark"><Send :size="18" /></span>
        <span><strong>投稿推荐</strong><small>Submission Recommendation Agent · 冲刺、匹配、保底投稿决策</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建推荐</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><Trophy :size="25" /></span>
        <p class="eyebrow">SUBMISSION RECOMMENDATION AGENT</p>
        <h1>从论文内容匹配合适投稿目标</h1>
        <p>输入论文标题、摘要、关键词、参考文献或研究描述，生成冲刺、匹配、保底三档推荐和投稿准备清单。</p>
      </div>
      <div class="innovation-run-controls">
        <label><span>目标级别</span><input v-model="targetLevels" type="text" placeholder="CCF-A,CCF-B" /></label>
        <label><span>审稿周期</span><input v-model.number="maxReviewWeeks" type="number" min="4" max="52" /></label>
        <label><span>实验完整度</span><input v-model.number="experimentCompleteness" type="number" min="0" max="1" step="0.05" /></label>
        <div class="innovation-mode-control">
          <span>创新层次</span>
          <div class="segment-control" role="tablist" aria-label="创新层次">
            <button type="button" :class="{ active: noveltyLevel === 'incremental' }" @click="noveltyLevel = 'incremental'">渐进</button>
            <button type="button" :class="{ active: noveltyLevel === 'substantial' }" @click="noveltyLevel = 'substantial'">显著</button>
            <button type="button" :class="{ active: noveltyLevel === 'breakthrough' }" @click="noveltyLevel = 'breakthrough'">突破</button>
          </div>
        </div>
        <label class="innovation-checkbox"><input v-model="preferOA" type="checkbox" />偏好开放获取</label>
      </div>
      <AgentPromptBox
        v-model="query"
        :busy="busy"
        placeholder="例如：论文题目、摘要、关键词、方法贡献、实验结果，以及希望投 CCF-A/B..."
        hint="特征提取 · venue 匹配 · 三档推荐 · 投稿清单"
        accept=".pdf,.doc,.docx,.txt,.md"
        @submit="startTask"
      />
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading">
          <span>投稿推荐任务</span>
          <strong>{{ task.progress }}%</strong>
          <h1>{{ task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="literature-plan manuscript-plan-card">
          <div class="literature-section-label"><Target :size="15" />推荐概览</div>
          <p>{{ output.submission_request?.paper?.title || task.prompt }}</p>
          <div class="agent-mini-metrics">
            <span><Trophy :size="14" />推荐 {{ recommendations.length }}</span>
            <span><CalendarClock :size="14" />周期 {{ output.submission_request?.preferences?.max_review_weeks ?? 12 }} 周</span>
            <span>{{ output.submission_request?.quality?.novelty_level || 'substantial' }}</span>
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
        <nav class="literature-result-tabs" aria-label="投稿推荐结果视图">
          <button type="button" :class="{ active: activeTab === 'venues' }" @click="activeTab = 'venues'"><Trophy :size="15" />推荐目标<span>{{ recommendations.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'strategy' }" @click="activeTab = 'strategy'"><ClipboardList :size="15" />策略清单</button>
          <button type="button" :class="{ active: activeTab === 'report' }" @click="activeTab = 'report'"><CheckCircle2 :size="15" />推荐报告</button>
        </nav>

        <div v-if="activeTab === 'venues'" class="agent-card-list">
          <article v-for="(item, index) in recommendations" :key="item.venue?.abbreviation || index" class="agent-result-card innovation-card">
            <span class="literature-paper-rank">{{ index + 1 }}</span>
            <div>
              <p class="status-tag">{{ tierLabel(item.tier) }} · {{ item.venue?.ccf_level || '未分级' }}</p>
              <h2>{{ item.venue?.abbreviation }} · {{ item.venue?.full_name }}</h2>
              <div class="innovation-score-grid">
                <span>综合 {{ scoreText(item.match_score?.overall) }}</span>
                <span>主题 {{ scoreText(item.match_score?.topic_similarity) }}</span>
                <span>方法 {{ scoreText(item.match_score?.methodology_alignment) }}</span>
                <span>置信 {{ scoreText(item.confidence) }}</span>
              </div>
              <p v-if="item.strengths?.length"><strong>优势：</strong>{{ item.strengths.join('；') }}</p>
              <p v-if="item.risks?.length"><strong>风险：</strong>{{ item.risks.join('；') }}</p>
              <p>{{ item.differentiation }}</p>
            </div>
          </article>
          <div v-if="recommendations.length === 0" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <Trophy v-else :size="24" />
            <strong>{{ isRunning ? '正在生成投稿推荐' : '尚未生成推荐' }}</strong>
            <span>{{ task.current_step }}</span>
          </div>
        </div>

        <div v-else-if="activeTab === 'strategy'" class="agent-card-list">
          <article class="agent-result-card">
            <span class="status-tag">TIMELINE</span>
            <h2>投稿时间线</h2>
            <p v-for="item in timeline" :key="`${item.phase}-${item.deadline}`">
              <CalendarClock :size="15" />{{ item.phase }} · {{ item.deadline || '待确认' }} · {{ tierLabel(String(item.tier || '')) }}
            </p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">CHECKLIST</span>
            <h2>准备清单</h2>
            <p v-for="item in checklist.format_checks ?? []" :key="item"><CheckCircle2 :size="15" />{{ item }}</p>
            <p v-for="item in checklist.experiment_supplements ?? []" :key="item"><CheckCircle2 :size="15" />{{ item }}</p>
            <p v-for="item in checklist.cover_letter_points ?? []" :key="item"><CheckCircle2 :size="15" />{{ item }}</p>
          </article>
          <article class="agent-result-card">
            <span class="status-tag">FALLBACK</span>
            <h2>备选策略</h2>
            <p>{{ output.submission_strategy?.fallback_plan || '暂未生成备选策略' }}</p>
          </article>
        </div>

        <article v-else class="literature-report-view">
          <template v-for="(block, index) in reportBlocks" :key="index">
            <h1 v-if="block.type === 'h1'">{{ block.text }}</h1>
            <h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2>
            <li v-else-if="block.type === 'li'">{{ block.text }}</li>
            <p v-else-if="block.type === 'p'">{{ block.text }}</p>
            <br v-else />
          </template>
        </article>
      </section>
    </div>
  </div>
</template>
