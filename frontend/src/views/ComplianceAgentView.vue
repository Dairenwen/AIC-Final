<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bot,
  CheckCircle2,
  ClipboardCheck,
  Download,
  FileCheck2,
  FileText,
  LoaderCircle,
  Quote,
  ShieldAlert,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { ComplianceModuleResult, ComplianceRisk, ResearchTask } from '@/types'

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

interface ComplianceSummary {
  compliance_score?: number
  summary?: string
  excellent_points?: string[]
  revision_suggestions?: Array<string | Record<string, unknown>>
}

interface ComplianceRiskSummary {
  overall_level?: string
  risk_count?: number
  severity_counts?: Record<string, number>
  module_counts?: Record<string, number>
}

interface CompliancePayload {
  compliance_summary?: ComplianceSummary
  summary?: ComplianceRiskSummary
  risks?: ComplianceRisk[]
  module_check_results?: Record<string, ComplianceModuleResult>
}

const route = useRoute()
const router = useRouter()
const query = ref('检查论文的学术规范、引用、图表一致性与投稿格式')
const complianceTaskType = ref<'paper_precheck' | 'journal_submission'>('paper_precheck')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'overview' | 'risks' | 'modules' | 'report'>('overview')
const riskModule = ref('all')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const legacyCompliance = computed(() => (output.value.academic_compliance ?? {}) as CompliancePayload)
const compliance = computed<ComplianceSummary>(() => output.value.compliance_summary ?? legacyCompliance.value.compliance_summary ?? {})
const riskSummary = computed<ComplianceRiskSummary>(() => output.value.risk_summary ?? legacyCompliance.value.summary ?? {})
const risks = computed<ComplianceRisk[]>(() => output.value.risks ?? legacyCompliance.value.risks ?? [])
const moduleResults = computed<Record<string, ComplianceModuleResult>>(() => output.value.module_check_results ?? legacyCompliance.value.module_check_results ?? {})
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const filteredRisks = computed(() => riskModule.value === 'all' ? risks.value : risks.value.filter((item) => item.module === riskModule.value))
const reportBlocks = computed(() => parseMarkdown(output.value.report_markdown ?? ''))
const modules = computed(() => [
  { code: 'paper_norm', name: '论文规范', result: moduleResults.value.paper_norm, count: riskSummary.value.module_counts?.paper_norm ?? 0 },
  { code: 'citation', name: '引用核验', result: moduleResults.value.citation, count: riskSummary.value.module_counts?.citation ?? 0 },
  { code: 'figure_table', name: '图表一致性', result: moduleResults.value.figure_table, count: riskSummary.value.module_counts?.figure_table ?? 0 },
  { code: 'format_submission', name: '投稿格式', result: moduleResults.value.format_submission, count: riskSummary.value.module_counts?.format_submission ?? 0 },
])

async function startTask(payload: AgentPromptPayload) {
  if (busy.value) return
  if (!payload.file) {
    errorMessage.value = '请上传待检测的 MD、TXT、DOCX 或 PDF 稿件'
    return
  }
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const formData = new FormData()
    formData.append('file', payload.file)
    const upload = await http.post<{ data: { uploadId: string; fileName: string } }>('/uploads/manuscripts', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const response = await http.post('/tasks', {
      prompt: payload.prompt,
      agent_code: 'academic_compliance',
      attachment: upload.data.data.fileName,
      attachment_id: upload.data.data.uploadId,
      model: payload.model,
      model_config_id: payload.model.startsWith('model_config:') ? payload.model.slice('model_config:'.length) : null,
      compliance_task_type: complianceTaskType.value,
      compliance_rule_set: 'default',
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
    'task.started', 'compliance.source_ready', 'compliance.parsing', 'compliance.rules_ready',
    'compliance.checks_started', 'compliance.checks_ready', 'compliance.summary_ready',
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

function resetWorkspace() {
  closeEvents?.()
  closeEvents = null
  task.value = null
  events.value = []
  errorMessage.value = ''
  void router.replace({ path: route.path })
}

function suggestionText(value: string | Record<string, unknown>) {
  if (typeof value === 'string') return value
  return String(value.action || value.suggestion || value.title || '')
}

function moduleName(code: string) {
  return modules.value.find((item) => item.code === code)?.name || code
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

watch(() => route.query.task, (value) => {
  closeEvents?.()
  closeEvents = null
  if (typeof value === 'string' && value) void loadTask(value)
  else task.value = null
}, { immediate: true })

onBeforeUnmount(() => closeEvents?.())
</script>

<template>
  <div class="literature-agent-view compliance-agent-view">
    <header class="literature-agent-header">
      <div><span class="literature-agent-mark"><ShieldAlert :size="18" /></span><span><strong>学术合规性检测</strong><small>Academic Compliance Agent · 规范、引用、图表与投稿检查</small></span></div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">检测新稿件</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FileCheck2 :size="25" /></span>
        <p class="eyebrow">ACADEMIC COMPLIANCE AGENT</p>
        <h1>在提交前完成系统化学术合规检查</h1>
        <p>上传论文稿件，检查论文规范、引用与参考文献、图表一致性和投稿格式。</p>
      </div>
      <div class="compliance-mode-control">
        <span>检测场景</span>
        <div class="segment-control" role="tablist" aria-label="学术合规检测场景">
          <button type="button" :class="{ active: complianceTaskType === 'paper_precheck' }" @click="complianceTaskType = 'paper_precheck'">论文预检</button>
          <button type="button" :class="{ active: complianceTaskType === 'journal_submission' }" @click="complianceTaskType = 'journal_submission'">投稿检查</button>
        </div>
      </div>
      <AgentPromptBox v-model="query" :busy="busy" allow-personal-models accept=".md,.txt,.docx,.pdf,application/pdf" placeholder="说明本次合规检测目标" hint="MD / TXT / DOCX / PDF · 四模块并行检查" @submit="startTask" />
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading"><span>合规检测任务</span><strong>{{ task.progress }}%</strong><h1>{{ output.compliance_request?.file_name || task.prompt }}</h1><div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div></div>
        <div class="literature-plan manuscript-plan-card">
          <div class="literature-section-label"><FileText :size="15" />稿件信息</div>
          <p>{{ output.compliance_request?.file_name || '正在确认稿件' }}</p>
          <div class="literature-keywords"><span>{{ output.compliance_request?.file_type?.toUpperCase() || 'DOCUMENT' }}</span><span>{{ output.compliance_request?.task_type === 'journal_submission' ? '投稿检查' : '论文预检' }}</span><span>默认规则集</span></div>
        </div>
        <div class="literature-event-log manuscript-event-log"><div class="literature-section-label"><Bot :size="15" />Agent 进度</div><ol><li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li><li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li></ol></div>
      </section>

      <section class="literature-result-pane">
        <nav class="literature-result-tabs" aria-label="合规检测结果视图">
          <button type="button" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'"><ClipboardCheck :size="15" />检测概览</button>
          <button type="button" :class="{ active: activeTab === 'risks' }" @click="activeTab = 'risks'"><ShieldAlert :size="15" />风险项<span>{{ risks.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'modules' }" @click="activeTab = 'modules'"><CheckCircle2 :size="15" />模块结果</button>
          <button type="button" :class="{ active: activeTab === 'report' }" @click="activeTab = 'report'"><FileText :size="15" />完整报告</button>
        </nav>

        <div v-if="activeTab === 'overview'" class="compliance-overview">
          <div v-if="output.compliance_summary" class="compliance-score-band"><div><strong>{{ compliance.compliance_score ?? 0 }}</strong><span>合规得分</span></div><div><strong>{{ riskSummary.overall_level || '极低' }}</strong><span>总体修改优先级</span></div><div><strong>{{ risks.length }}</strong><span>风险项</span></div><div><strong>{{ modules.filter((item) => (item.result?.score ?? 0) >= 80).length }}</strong><span>达标模块</span></div></div>
          <div v-else class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><ShieldAlert v-else :size="24" /><strong>{{ isRunning ? '正在执行合规检查' : '尚未生成检查结果' }}</strong><span>{{ task.current_step }}</span></div>
          <article v-if="compliance.summary" class="compliance-summary-copy"><h2>检测结论</h2><p>{{ compliance.summary }}</p></article>
          <div class="compliance-two-column">
            <section><h2>优秀点</h2><p v-for="item in compliance.excellent_points ?? []" :key="item"><CheckCircle2 :size="14" />{{ item }}</p><p v-if="!compliance.excellent_points?.length">暂无明确优秀点</p></section>
            <section><h2>修改建议</h2><p v-for="(item, index) in compliance.revision_suggestions ?? []" :key="index"><ShieldAlert :size="14" />{{ suggestionText(item) }}</p><p v-if="!compliance.revision_suggestions?.length">暂无修改建议</p></section>
          </div>
          <div v-if="task.status === 'SUCCEEDED'" class="compliance-downloads"><a :href="apiUrl(`/tasks/${task.id}/artifacts/compliance-report`)"><Download :size="14" />下载 Markdown 报告</a><a :href="apiUrl(`/tasks/${task.id}/artifacts/compliance-json`)"><Download :size="14" />下载 JSON 结果</a></div>
        </div>

        <div v-else-if="activeTab === 'risks'" class="agent-card-list compliance-risk-view">
          <div class="compliance-risk-toolbar"><label><span>检查模块</span><select v-model="riskModule"><option value="all">全部模块</option><option v-for="item in modules" :key="item.code" :value="item.code">{{ item.name }}</option></select></label><span>{{ filteredRisks.length }} 项</span></div>
          <article v-for="risk in filteredRisks" :key="risk.risk_id" class="agent-result-card compliance-risk-card">
            <div class="compliance-risk-heading"><span class="compliance-severity" :class="`compliance-severity--${risk.severity}`">{{ risk.severity }}</span><small>{{ moduleName(risk.module) }} · {{ risk.risk_id }}</small></div>
            <h2>{{ risk.title }}</h2><p v-if="risk.location?.section"><strong>位置：</strong>{{ risk.location.section }}</p><blockquote v-if="risk.evidence?.[0]?.content"><Quote :size="14" />{{ risk.evidence[0].content }}</blockquote><p><strong>修改建议：</strong>{{ risk.suggestion }}</p>
          </article>
          <div v-if="filteredRisks.length === 0" class="literature-result-empty"><CheckCircle2 :size="24" /><strong>该范围内未发现风险</strong></div>
        </div>

        <div v-else-if="activeTab === 'modules'" class="compliance-module-grid">
          <article v-for="item in modules" :key="item.code" class="agent-result-card"><span class="status-tag">{{ item.count }} 项风险</span><h2>{{ item.name }}</h2><strong class="compliance-module-score">{{ item.result?.score ?? 0 }}</strong><p>{{ item.result?.summary || '模块检查已完成' }}</p></article>
        </div>

        <article v-else class="literature-report-view compliance-report-view">
          <div v-if="!output.report_markdown" class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><FileText v-else :size="24" /><strong>完整报告尚未生成</strong></div>
          <template v-for="(block, index) in reportBlocks" :key="index"><h1 v-if="block.type === 'h1'">{{ block.text }}</h1><h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2><h3 v-else-if="block.type === 'h3'">{{ block.text }}</h3><li v-else-if="block.type === 'li'">{{ block.text }}</li><p v-else-if="block.type === 'p'">{{ block.text }}</p><br v-else /></template>
        </article>
      </section>
    </div>
  </div>
</template>
