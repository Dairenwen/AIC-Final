<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bot,
  Check,
  CheckCircle2,
  Download,
  FileBadge2,
  FileText,
  Gavel,
  LoaderCircle,
  SearchCheck,
  ShieldCheck,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { PatentCandidate, PatentClaim, ResearchTask } from '@/types'

interface AgentPromptPayload {
  prompt: string
  model: string
  attachment: string | null
  link: string | null
  file: File | null
}

interface TaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
}

const route = useRoute()
const router = useRouter()
const query = ref('请围绕技术问题、核心结构或步骤、关键参数、技术效果和可选实施方式生成专利交底书与权利要求草案。')
const patentTitle = ref('')
const workflowMode = ref<'flow_first' | 'strict'>('flow_first')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const selecting = ref('')
const selectionNotes = ref('')
const errorMessage = ref('')
const activeTab = ref<'overview' | 'disclosure' | 'claims' | 'validation'>('overview')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const candidates = computed<PatentCandidate[]>(() => output.value.patent_candidates ?? [])
const selectedCandidate = computed(() => candidates.value.find((item) => item.id === output.value.selected_patent_point_id))
const claimItems = computed<PatentClaim[]>(() => output.value.claims?.claims ?? [])
const validation = computed(() => output.value.claim_validation ?? {})
const waitingForSelection = computed(() => task.value?.status === 'WAITING_INPUT')
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(task.value.status))
const disclosureBlocks = computed(() => parseMarkdown(output.value.disclosure_markdown ?? ''))
const searchStatus = computed(() => String(output.value.patent_summary?.search_status ?? 'not_started'))

async function startTask(payload: AgentPromptPayload) {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    let attachmentId: string | null = null
    let attachmentName: string | null = null
    if (payload.file) {
      const formData = new FormData()
      formData.append('file', payload.file)
      const upload = await http.post<{ data: { uploadId: string; fileName: string } }>('/uploads/patents', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      attachmentId = upload.data.data.uploadId
      attachmentName = upload.data.data.fileName
    }
    const response = await http.post('/tasks', {
      prompt: payload.prompt,
      agent_code: 'patent_drafting',
      attachment: attachmentName,
      attachment_id: attachmentId,
      model: payload.model,
      model_config_id: payload.model.startsWith('model_config:') ? payload.model.slice('model_config:'.length) : null,
      patent_title: patentTitle.value || attachmentName?.replace(/\.[^.]+$/, '') || payload.prompt.slice(0, 120),
      patent_workflow_mode: workflowMode.value,
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

async function chooseCandidate(candidate: PatentCandidate) {
  if (!task.value || selecting.value) return
  selecting.value = candidate.id
  errorMessage.value = ''
  try {
    const response = await http.post(`/tasks/${task.value.id}/patent-selection`, {
      selected_id: candidate.id,
      notes: selectionNotes.value,
    })
    task.value = response.data.data as ResearchTask
    subscribe(task.value.id)
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    selecting.value = ''
  }
}

async function loadTask(taskId: string) {
  errorMessage.value = ''
  try {
    const response = await http.get(`/tasks/${taskId}`)
    task.value = response.data.data as ResearchTask
    query.value = task.value.prompt
    if (!['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(task.value.status)) subscribe(taskId)
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
    'task.started', 'patent.materials_ready', 'patent.candidates_started', 'patent.selection_required',
    'patent.selection_accepted', 'patent.drafting_started', 'patent.artifacts_ready', 'task.completed', 'task.failed',
  ]
  const handle = (event: Event) => {
    const payload = JSON.parse((event as MessageEvent).data) as TaskEvent
    if (!events.value.some((item) => item.sequence === payload.sequence)) events.value.push(payload)
    if (task.value) {
      task.value.progress = payload.progress
      task.value.current_step = payload.message
    }
    void refreshTask()
    if (['patent.selection_required', 'task.completed', 'task.failed'].includes(payload.type)) source.close()
  }
  eventTypes.forEach((eventType) => source.addEventListener(eventType, handle))
  source.onerror = () => {
    void refreshTask().finally(() => {
      if (task.value && ['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(task.value.status)) source.close()
    })
  }
  closeEvents = () => source.close()
}

function resetWorkspace() {
  closeEvents?.()
  closeEvents = null
  task.value = null
  events.value = []
  selectionNotes.value = ''
  errorMessage.value = ''
  void router.replace({ path: route.path })
}

function parseMarkdown(markdown: string) {
  return markdown.split('\n').map((line) => {
    const text = line.trim().replaceAll('**', '')
    if (text.startsWith('### ')) return { type: 'h3', text: text.slice(4) }
    if (text.startsWith('## ')) return { type: 'h2', text: text.slice(3) }
    if (text.startsWith('# ')) return { type: 'h1', text: text.slice(2) }
    if (text.startsWith('- ')) return { type: 'li', text: text.slice(2) }
    if (text.startsWith('> ')) return { type: 'quote', text: text.slice(2) }
    return { type: text ? 'p' : 'space', text }
  })
}

function claimTypeLabel(type?: string) {
  if (type?.includes('independent')) return '独立权利要求'
  if (type?.includes('dependent')) return '从属权利要求'
  return type || '权利要求'
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '请求失败'
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
  <div class="literature-agent-view patent-agent-view">
    <header class="literature-agent-header">
      <div><span class="literature-agent-mark"><Gavel :size="18" /></span><span><strong>专利撰写</strong><small>Patent Drafting Agent · 技术交底书与权利要求</small></span></div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">新建专利任务</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FileBadge2 :size="25" /></span>
        <p class="eyebrow">PATENT DRAFTING AGENT</p>
        <h1>从技术材料形成可审阅的专利草案</h1>
        <p>候选专利点、现有技术检索、技术交底书、权利要求与证据复核。</p>
      </div>
      <div class="patent-task-options">
        <label><span>技术方案名称</span><input v-model="patentTitle" type="text" maxlength="200" placeholder="例如：一种面向分层缓存的自适应迁移方法" /></label>
        <div><span>工作流模式</span><div class="segment-control" role="tablist" aria-label="专利撰写工作流模式"><button type="button" :class="{ active: workflowMode === 'flow_first' }" @click="workflowMode = 'flow_first'">连续生成</button><button type="button" :class="{ active: workflowMode === 'strict' }" @click="workflowMode = 'strict'">严格校验</button></div></div>
      </div>
      <AgentPromptBox v-model="query" :busy="busy" allow-personal-models accept=".md,.markdown,.txt,.docx,.pptx,.ppsx,.pdf,.py,.go,.java,.js,.ts,.tsx,.rs,.c,.h,.cpp,.hpp" placeholder="填写技术问题、核心方案、关键参数与技术效果" hint="可附加技术文档、说明书、演示文稿或源代码" @submit="startTask" />
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading"><span>专利撰写任务</span><strong>{{ task.progress }}%</strong><h1>{{ selectedCandidate?.title || task.title }}</h1><div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div></div>
        <div class="literature-plan manuscript-plan-card">
          <div class="literature-section-label"><SearchCheck :size="15" />运行状态</div>
          <p>{{ task.current_step || '等待执行' }}</p>
          <div class="literature-keywords"><span>{{ output.patent_run_id || 'RUN PENDING' }}</span><span>{{ searchStatus }}</span><span>{{ output.patent_summary?.workflow_mode || 'flow_first' }}</span></div>
        </div>
        <div class="literature-event-log manuscript-event-log"><div class="literature-section-label"><Bot :size="15" />Agent 进度</div><ol><li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li><li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li></ol></div>
      </section>

      <section class="literature-result-pane">
        <nav class="literature-result-tabs" aria-label="专利撰写结果视图">
          <button type="button" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'"><FileBadge2 :size="15" />专利点</button>
          <button type="button" :class="{ active: activeTab === 'disclosure' }" @click="activeTab = 'disclosure'"><FileText :size="15" />交底书</button>
          <button type="button" :class="{ active: activeTab === 'claims' }" @click="activeTab = 'claims'"><Gavel :size="15" />权利要求<span>{{ claimItems.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'validation' }" @click="activeTab = 'validation'"><ShieldCheck :size="15" />校验</button>
        </nav>

        <div v-if="activeTab === 'overview'" class="patent-overview">
          <div v-if="isRunning" class="literature-result-empty"><LoaderCircle class="spin" :size="24" /><strong>Agent 正在执行</strong><span>{{ task.current_step }}</span></div>
          <template v-else-if="waitingForSelection">
            <div class="patent-selection-heading"><div><span class="status-tag">需要人工决策</span><h2>选择本次撰写的核心专利点</h2></div><textarea v-model="selectionNotes" rows="2" maxlength="2000" placeholder="选择说明（可选）"></textarea></div>
            <div class="agent-card-list patent-candidate-list">
              <article v-for="candidate in candidates" :key="candidate.id" class="agent-result-card patent-candidate-card">
                <div><span class="status-tag">{{ candidate.id }}</span><h2>{{ candidate.title }}</h2></div>
                <dl><template v-if="candidate.innovation"><dt>创新构思</dt><dd>{{ candidate.innovation }}</dd></template><template v-if="candidate.difference"><dt>差异方向</dt><dd>{{ candidate.difference }}</dd></template><template v-if="candidate.feasibility"><dt>可实施性</dt><dd>{{ candidate.feasibility }}</dd></template></dl>
                <button class="primary-button" type="button" :disabled="Boolean(selecting)" @click="chooseCandidate(candidate)"><LoaderCircle v-if="selecting === candidate.id" class="spin" :size="15" /><Check v-else :size="15" />选择此专利点</button>
              </article>
            </div>
          </template>
          <template v-else-if="task.status === 'SUCCEEDED'">
            <div class="compliance-score-band"><div><strong>{{ claimItems.length }}</strong><span>权利要求</span></div><div><strong>{{ validation.passed ? '通过' : '复核' }}</strong><span>确定性校验</span></div><div><strong>{{ output.patent_warnings?.length ?? 0 }}</strong><span>提示项</span></div><div><strong>{{ searchStatus }}</strong><span>检索状态</span></div></div>
            <article class="patent-selected-summary"><span class="status-tag">{{ output.selected_patent_point_id }}</span><h2>{{ selectedCandidate?.title }}</h2><p>{{ selectedCandidate?.innovation }}</p></article>
            <div class="compliance-downloads"><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-disclosure-docx`)"><Download :size="14" />技术交底书 DOCX</a><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-claims-markdown`)"><Download :size="14" />权利要求 Markdown</a><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-manifest`)"><Download :size="14" />产物清单</a></div>
          </template>
          <div v-else class="literature-result-empty"><FileBadge2 :size="24" /><strong>任务未完成</strong><span>{{ task.error || task.current_step }}</span></div>
          <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
        </div>

        <article v-else-if="activeTab === 'disclosure'" class="literature-report-view patent-disclosure-view">
          <div v-if="!output.disclosure_markdown" class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><FileText v-else :size="24" /><strong>技术交底书尚未生成</strong></div>
          <template v-for="(block, index) in disclosureBlocks" :key="index"><h1 v-if="block.type === 'h1'">{{ block.text }}</h1><h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2><h3 v-else-if="block.type === 'h3'">{{ block.text }}</h3><li v-else-if="block.type === 'li'">{{ block.text }}</li><blockquote v-else-if="block.type === 'quote'">{{ block.text }}</blockquote><p v-else-if="block.type === 'p'">{{ block.text }}</p><br v-else /></template>
        </article>

        <div v-else-if="activeTab === 'claims'" class="agent-card-list patent-claims-list">
          <article v-for="claim in claimItems" :key="claim.claim_id || claim.claim_number" class="agent-result-card patent-claim-card"><div><span class="status-tag">权利要求 {{ claim.claim_number }}</span><small>{{ claimTypeLabel(claim.claim_type) }}</small></div><p>{{ claim.text }}</p><footer v-if="claim.depends_on?.length">引用权利要求 {{ claim.depends_on.join('、') }}</footer></article>
          <div v-if="claimItems.length === 0" class="literature-result-empty"><Gavel :size="24" /><strong>权利要求尚未生成</strong></div>
        </div>

        <div v-else class="patent-validation-view">
          <div v-if="output.claim_validation" class="compliance-score-band"><div><strong>{{ validation.passed ? 'PASS' : 'REVIEW' }}</strong><span>校验结论</span></div><div><strong>{{ validation.issue_count ?? validation.issues?.length ?? 0 }}</strong><span>问题</span></div><div><strong>{{ validation.warning_count ?? validation.warnings?.length ?? 0 }}</strong><span>警告</span></div><div><strong>{{ output.patent_warnings?.length ?? 0 }}</strong><span>运行提示</span></div></div>
          <section class="patent-review-notice"><CheckCircle2 :size="18" /><div><h2>专业复核状态</h2><p>确定性规则通过不代表新颖性、创造性、授权概率或不侵权结论，交付前仍需专利专业人员审核。</p></div></section>
          <div class="compliance-downloads" v-if="task.status === 'SUCCEEDED'"><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-disclosure-evidence`)"><Download :size="14" />交底书证据复核</a><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-claim-evidence`)"><Download :size="14" />权利要求证据复核</a><a :href="apiUrl(`/tasks/${task.id}/artifacts/patent-claims-json`)"><Download :size="14" />结构化权利要求</a></div>
        </div>
      </section>
    </div>
  </div>
</template>
