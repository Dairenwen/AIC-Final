<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bot, CheckCircle2, ClipboardCheck, FilePenLine, LoaderCircle, MessageSquareReply, Rows3 } from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { ResearchTask, ReviewerCommentItem } from '@/types'

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
const replyMode = ref<'full' | 'analysis' | 'reply'>('full')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'comments' | 'reply' | 'checklist'>('comments')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const reviewItems = computed<ReviewerCommentItem[]>(() => output.value.review_items ?? [])
const checklist = computed(() => output.value.revision_checklist ?? [])
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const replyBlocks = computed(() => parseMarkdown(output.value.response_letter_markdown ?? ''))

async function startTask(payload?: AgentPromptPayload | Event) {
  const agentPayload = isAgentPromptPayload(payload) ? payload : null
  const prompt = agentPayload?.prompt ?? query.value.trim()
  if (!prompt || busy.value) return
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    let attachmentId: string | null = null
    let attachmentName = agentPayload?.attachment ?? null
    if (agentPayload?.file) {
      const formData = new FormData()
      formData.append('file', agentPayload.file)
      const upload = await http.post<{ data: { uploadId: string; fileName: string } }>('/uploads/manuscripts', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      attachmentId = upload.data.data.uploadId
      attachmentName = upload.data.data.fileName
    }
    const response = await http.post('/tasks', {
      prompt,
      agent_code: 'reviewer_comments',
      model: agentPayload?.model ?? 'vertical_domain',
      attachment: attachmentName,
      attachment_id: attachmentId,
      link: agentPayload?.link ?? null,
      reviewer_reply_mode: replyMode.value,
    })
    task.value = response.data.data as ResearchTask
    activeTab.value = replyMode.value === 'analysis' ? 'comments' : 'reply'
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
    'reviewer.comments_split',
    'reviewer.analysis_ready',
    'reviewer.strategy_ready',
    'reviewer.reply_ready',
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

function parseMarkdown(markdown: string) {
  return markdown.split('\n').map((line) => {
    const text = line.trim().replaceAll('**', '')
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
  <div class="literature-agent-view reviewer-comments-agent-view">
    <header class="literature-agent-header">
      <div>
        <span class="literature-agent-mark"><MessageSquareReply :size="18" /></span>
        <span><strong>审稿意见解析与引导回复</strong><small>Reviewer Comments Agent · 意见拆解、返修策略与回复信</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建回复</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FilePenLine :size="25" /></span>
        <p class="eyebrow">REVIEWER COMMENTS AGENT</p>
        <h1>从审稿意见生成逐条返修回复</h1>
        <p>适合粘贴审稿意见、决定修改优先级、生成回复策略、回复信草稿和返修执行清单。</p>
      </div>
      <div class="innovation-run-controls">
        <div class="innovation-mode-control">
          <span>处理模式</span>
          <div class="segment-control" role="tablist" aria-label="审稿意见处理模式">
            <button type="button" :class="{ active: replyMode === 'full' }" @click="replyMode = 'full'">完整回复</button>
            <button type="button" :class="{ active: replyMode === 'analysis' }" @click="replyMode = 'analysis'">仅解析</button>
            <button type="button" :class="{ active: replyMode === 'reply' }" @click="replyMode = 'reply'">仅草稿</button>
          </div>
        </div>
      </div>
      <AgentPromptBox
        v-model="query"
        :busy="busy"
        placeholder="粘贴审稿意见，例如：Reviewer 1 建议补充更多基线实验，并说明方法复杂度..."
        hint="意见拆解 · 严重程度 · 回复策略 · 回复信草稿"
        accept=".pdf,.docx,.txt,.md"
        @submit="startTask"
      />
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace reviewer-comments-workspace">
      <section class="literature-trace-pane reviewer-comments-trace-pane">
        <div class="literature-task-heading reviewer-comments-task-heading">
          <span>返修回复任务</span>
          <strong>{{ task.progress }}%</strong>
          <h1>{{ task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="literature-plan manuscript-plan-card reviewer-comments-summary-card">
          <div class="literature-section-label"><Rows3 :size="15" />解析摘要</div>
          <p>共识别 {{ reviewItems.length }} 条审稿意见，需要按优先级逐条回复并绑定正文修改。</p>
          <div class="agent-mini-metrics">
            <span>重大 {{ output.metrics?.major_count ?? 0 }}</span>
            <span>阻塞 {{ output.metrics?.blocking_count ?? 0 }}</span>
            <span>轻微 {{ output.metrics?.minor_count ?? 0 }}</span>
          </div>
        </div>

        <div class="literature-event-log manuscript-event-log reviewer-comments-event-log">
          <div class="literature-section-label"><Bot :size="15" />Agent 进度</div>
          <ol>
            <li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li>
            <li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li>
          </ol>
        </div>
      </section>

      <section class="literature-result-pane">
        <nav class="literature-result-tabs" aria-label="审稿意见回复结果视图">
          <button type="button" :class="{ active: activeTab === 'comments' }" @click="activeTab = 'comments'"><MessageSquareReply :size="15" />意见拆解<span>{{ reviewItems.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'reply' }" @click="activeTab = 'reply'"><FilePenLine :size="15" />回复信</button>
          <button type="button" :class="{ active: activeTab === 'checklist' }" @click="activeTab = 'checklist'"><ClipboardCheck :size="15" />返修清单</button>
        </nav>

        <div v-if="activeTab === 'comments'" class="agent-card-list">
          <article v-for="item in reviewItems" :key="item.id" class="agent-result-card">
            <span class="status-tag">{{ item.severity }} · {{ item.category }}</span>
            <h2>{{ item.id }}</h2>
            <p>{{ item.comment }}</p>
            <p><strong>回复角度：</strong>{{ item.reply_angle }}</p>
            <div class="member-list">
              <span v-for="evidence in item.evidence_needed" :key="evidence">{{ evidence }}</span>
            </div>
          </article>
          <div v-if="reviewItems.length === 0" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <MessageSquareReply v-else :size="24" />
            <strong>{{ isRunning ? '正在解析审稿意见' : '尚未生成意见拆解' }}</strong>
            <span>{{ task.current_step }}</span>
          </div>
        </div>

        <article v-else-if="activeTab === 'reply'" class="literature-report-view">
          <div v-if="!output.response_letter_markdown" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <FilePenLine v-else :size="24" />
            <strong>{{ isRunning ? '正在生成回复信' : '当前模式未生成回复信' }}</strong>
            <span>{{ task.current_step }}</span>
          </div>
          <template v-for="(block, index) in replyBlocks" :key="index">
            <h1 v-if="block.type === 'h1'">{{ block.text }}</h1>
            <h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2>
            <li v-else-if="block.type === 'li'">{{ block.text }}</li>
            <p v-else-if="block.type === 'p'">{{ block.text }}</p>
            <br v-else />
          </template>
        </article>

        <div v-else class="agent-card-list">
          <article v-for="item in checklist" :key="item.id" class="agent-result-card">
            <span class="status-tag">{{ item.priority }}</span>
            <h2>{{ item.id }}</h2>
            <p><CheckCircle2 :size="15" />{{ item.action }}</p>
            <p>{{ item.evidence }}</p>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>
