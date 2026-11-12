<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  BookOpenCheck,
  Bot,
  CheckCircle2,
  FileSearch,
  FileText,
  FlaskConical,
  LoaderCircle,
  MessageSquareText,
  Microscope,
  Quote,
  ShieldCheck,
  TableProperties,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { PaperReadingEvidence, ResearchTask } from '@/types'

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

interface UploadResponse {
  uploadId: string
  fileName: string
  size: number
}

const route = useRoute()
const router = useRouter()
const query = ref('理解论文的研究问题、方法结构、主要实验、创新点与局限')
const speedProfile = ref<'fast' | 'balanced' | 'quality'>('balanced')
const followUpQuestion = ref('')
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<'overview' | 'evidence' | 'analysis' | 'diagnostics' | 'report'>('overview')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const report = computed(() => output.value.paper_reading)
const readingResult = computed(() => report.value?.reading_result)
const claims = computed(() => readingResult.value?.claims ?? [])
const evidence = computed(() => readingResult.value?.evidence ?? [])
const evidenceMap = computed(() => new Map(evidence.value.map((item) => [item.evidence_id, item])))
const scientificElements = computed(() => report.value?.scientific_elements?.elements ?? [])
const experimentAnalysis = computed(() => report.value?.experiments)
const reliabilityRecords = computed(() => report.value?.core_reliability?.records ?? [])
const stageEntries = computed(() => Object.entries(report.value?.flow_execution?.stages ?? {}))
const timingStages = computed(() => Object.entries(output.value.timing?.stages_seconds ?? {}))
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const markdownBlocks = computed(() => parseMarkdown(output.value.report_markdown ?? ''))
const overviewSections = computed(() => [
  { title: '研究问题', values: readingResult.value?.research_questions ?? [] },
  { title: '方法结构', values: readingResult.value?.method_structure ?? [] },
  { title: '主要实验', values: readingResult.value?.experiment_findings ?? [] },
  { title: '创新点', values: readingResult.value?.innovations ?? [] },
  { title: '局限性', values: readingResult.value?.limitations ?? [] },
])

async function startTask(payload: AgentPromptPayload) {
  if (busy.value) return
  if (!payload.file && !isArxivLink(payload.link)) {
    errorMessage.value = '请上传 PDF 文件或添加有效的 arXiv 论文链接'
    return
  }

  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    let uploadId: string | null = null
    if (payload.file) {
      const formData = new FormData()
      formData.append('file', payload.file)
      const upload = await http.post<{ data: UploadResponse }>('/uploads/papers', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      uploadId = upload.data.data.uploadId
    }

    const response = await http.post('/tasks', {
      prompt: payload.prompt,
      agent_code: 'paper_reading',
      model: payload.model,
      model_config_id: payload.model.startsWith('model_config:') ? payload.model.slice('model_config:'.length) : null,
      attachment: payload.attachment,
      attachment_id: uploadId,
      link: payload.link,
      speed_profile: speedProfile.value,
      follow_up_question: followUpQuestion.value.trim() || null,
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
    'task.started',
    'paper.source_ready',
    'paper.parsing',
    'paper.analysis_ready',
    'paper.evidence_validated',
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
  errorMessage.value = ''
  void router.replace({ path: route.path })
}

function evidenceFor(ids: string[]) {
  return ids.map((id) => evidenceMap.value.get(id)).filter((item): item is PaperReadingEvidence => Boolean(item))
}

function isArxivLink(value: string | null) {
  return Boolean(value && /arxiv\.org\/(abs|pdf)\//i.test(value))
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
  <div class="literature-agent-view paper-reading-view">
    <header class="literature-agent-header">
      <div>
        <span class="literature-agent-mark"><BookOpenCheck :size="18" /></span>
        <span><strong>论文精读</strong><small>Paper Reading Agent · 单篇论文证据化深度阅读</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">精读新论文</button>
    </header>

    <section v-if="!task" class="literature-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><FileSearch :size="25" /></span>
        <p class="eyebrow">PAPER READING AGENT</p>
        <h1>从原文证据出发，读懂一篇论文</h1>
        <p>支持文本型 PDF 与 arXiv 论文，输出研究问题、方法、实验、创新与局限，并保留页码和章节证据。</p>
      </div>

      <div class="reading-profile-control">
        <span>阅读档位</span>
        <div class="segment-control" role="tablist" aria-label="阅读档位">
          <button type="button" :class="{ active: speedProfile === 'fast' }" @click="speedProfile = 'fast'">快速</button>
          <button type="button" :class="{ active: speedProfile === 'balanced' }" @click="speedProfile = 'balanced'">标准</button>
          <button type="button" :class="{ active: speedProfile === 'quality' }" @click="speedProfile = 'quality'">深度</button>
        </div>
      </div>

      <label class="paper-reading-question-field">
        <span><MessageSquareText :size="15" />论文内问答</span>
        <input v-model="followUpQuestion" maxlength="1000" placeholder="可选：输入一个需要基于原文证据回答的问题" />
      </label>

      <AgentPromptBox
        v-model="query"
        :busy="busy"
        allow-personal-models
        accept=".pdf,application/pdf"
        placeholder="说明本次精读目标"
        hint="上传 PDF 或添加 arXiv 链接 · 单篇论文 · Claim-Evidence"
        @submit="startTask"
      />
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace">
      <section class="literature-trace-pane">
        <div class="literature-task-heading">
          <span>论文精读任务</span><strong>{{ task.progress }}%</strong>
          <h1>{{ report?.paper.title || output.reading_source?.fileName || output.reading_source?.arxivId || task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="literature-plan manuscript-plan-card paper-source-card">
          <div class="literature-section-label"><FileText :size="15" />论文来源</div>
          <p>{{ output.reading_source?.type === 'ARXIV' ? `arXiv:${output.reading_source.arxivId}` : output.reading_source?.fileName || '正在确认来源' }}</p>
          <div class="literature-keywords">
            <span>{{ report?.request.depth || speedProfile }}</span>
            <span>v{{ output.paper_reading_agent_version || '0.6.4' }}</span>
            <span v-for="aspect in report?.request.focus_aspects?.slice(0, 4) ?? []" :key="aspect">{{ aspect }}</span>
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
        <nav class="literature-result-tabs" aria-label="精读结果视图">
          <button type="button" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'"><BookOpenCheck :size="15" />阅读概览</button>
          <button type="button" :class="{ active: activeTab === 'evidence' }" @click="activeTab = 'evidence'"><ShieldCheck :size="15" />结论证据<span>{{ claims.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'analysis' }" @click="activeTab = 'analysis'"><Microscope :size="15" />图表实验<span>{{ scientificElements.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'diagnostics' }" @click="activeTab = 'diagnostics'"><Activity :size="15" />可靠性</button>
          <button type="button" :class="{ active: activeTab === 'report' }" @click="activeTab = 'report'"><FileText :size="15" />完整报告</button>
        </nav>

        <div v-if="activeTab === 'overview'" class="paper-reading-overview">
          <div v-if="!report" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" /><BookOpenCheck v-else :size="24" />
            <strong>{{ isRunning ? '正在解析与精读论文' : task.error || '尚未生成精读报告' }}</strong><span>{{ task.current_step }}</span>
            <small v-if="task.error_detail?.message">{{ task.error_detail.message }}</small>
          </div>
          <template v-else>
            <section class="paper-reading-summary">
              <span class="status-tag">{{ report.flow_execution?.completion_status || 'COMPLETED' }}</span>
              <h2>{{ report.paper.title }}</h2>
              <p>{{ report.paper.authors.join('、') }}<template v-if="report.paper.year"> · {{ report.paper.year }}</template></p>
              <blockquote v-if="report.narrative?.one_sentence_summary"><Quote :size="17" />{{ report.narrative.one_sentence_summary }}</blockquote>
              <div class="paper-reading-metrics">
                <span><strong>{{ claims.length }}</strong>可靠结论</span>
                <span><strong>{{ evidence.length }}</strong>原文证据</span>
                <span><strong>{{ scientificElements.length }}</strong>科学对象</span>
                <span><strong>{{ reliabilityRecords.length }}</strong>可靠性记录</span>
              </div>
            </section>
            <section v-if="report.qa_response" class="paper-reading-qa">
              <div><MessageSquareText :size="16" /><span>{{ report.qa_response.answer_status }}</span></div>
              <h3>{{ report.qa_response.question }}</h3>
              <p>{{ report.qa_response.answer }}</p>
            </section>
            <div class="paper-reading-section-grid">
              <section v-for="section in overviewSections" :key="section.title">
                <h3>{{ section.title }}</h3>
                <p v-for="claimId in section.values" :key="claimId">{{ claims.find((item) => item.claim_id === claimId)?.content || claimId }}</p>
                <p v-if="section.values.length === 0" class="muted-copy">暂无可靠结论</p>
              </section>
            </div>
          </template>
        </div>

        <div v-else-if="activeTab === 'evidence'" class="agent-card-list paper-claim-list">
          <article v-for="claim in claims" :key="claim.claim_id" class="agent-result-card">
            <div class="paper-claim-heading"><span class="status-tag">{{ claim.claim_type }}</span><small>{{ claim.claim_source }}</small></div>
            <h2>{{ claim.content }}</h2>
            <div class="paper-evidence-list">
              <div v-for="item in evidenceFor(claim.evidence_ids)" :key="item.evidence_id">
                <span><CheckCircle2 :size="14" />p.{{ item.page_number }} · {{ item.section_path.join(' / ') }}</span>
                <p>{{ item.evidence_text }}</p>
              </div>
            </div>
          </article>
          <div v-if="claims.length === 0" class="literature-result-empty"><FlaskConical :size="24" /><strong>证据绑定尚未完成</strong></div>
        </div>

        <div v-else-if="activeTab === 'analysis'" class="paper-reading-analysis">
          <section v-if="experimentAnalysis" class="paper-experiment-summary">
            <div class="paper-analysis-heading"><FlaskConical :size="17" /><h2>实验与复现</h2></div>
            <div class="paper-reading-metrics">
              <span><strong>{{ experimentAnalysis.datasets.length }}</strong>数据集</span>
              <span><strong>{{ experimentAnalysis.baselines.length }}</strong>基线</span>
              <span><strong>{{ experimentAnalysis.metrics.length }}</strong>指标</span>
              <span><strong>{{ experimentAnalysis.findings.length }}</strong>实验发现</span>
            </div>
            <div class="paper-analysis-facts">
              <p v-for="item in experimentAnalysis.findings" :key="item.content"><span>{{ item.finding_type }}</span>{{ item.content }}</p>
              <p v-for="item in experimentAnalysis.reproducibility.missing_information" :key="item"><span>待补信息</span>{{ item }}</p>
            </div>
          </section>
          <article v-for="element in scientificElements" :key="element.element_id" class="paper-scientific-element">
            <div class="paper-analysis-heading">
              <TableProperties v-if="element.element_type === 'TABLE'" :size="17" />
              <Microscope v-else :size="17" />
              <h2>{{ element.label }}</h2>
              <span class="status-tag">{{ element.visual_status }}</span>
            </div>
            <small>{{ element.element_type }} · p.{{ element.page }}</small>
            <p>{{ element.explanation }}</p>
            <ul v-if="element.findings.length"><li v-for="finding in element.findings" :key="finding">{{ finding }}</li></ul>
            <div v-if="element.table_checks.length || element.table_cell_facts.length" class="paper-table-facts">
              <p v-for="check in element.table_checks" :key="`${check.metric}-${check.scope}-${check.target_label}`">
                <strong>{{ check.metric }}</strong><span>{{ check.target_label }} {{ check.target_value }} / {{ check.baseline_label }} {{ check.baseline_value }}</span><small>差值 {{ check.absolute_difference }}</small>
              </p>
              <p v-for="fact in element.table_cell_facts" :key="`${fact.metric}-${fact.row_label}-${fact.column_header}`">
                <strong>{{ fact.metric }}</strong><span>{{ fact.row_label }} · {{ fact.column_header }}</span><small>{{ fact.value }}</small>
              </p>
            </div>
          </article>
          <div v-if="!experimentAnalysis && scientificElements.length === 0" class="literature-result-empty"><Microscope :size="24" /><strong>当前档位未请求图表与实验分析</strong></div>
        </div>

        <div v-else-if="activeTab === 'diagnostics'" class="paper-reading-diagnostics">
          <section class="paper-stage-list">
            <div class="paper-analysis-heading"><Activity :size="17" /><h2>执行阶段</h2><span class="status-tag">{{ report?.flow_execution?.completion_status || 'PENDING' }}</span></div>
            <p v-for="([name, status]) in stageEntries" :key="name"><span>{{ name }}</span><strong>{{ status }}</strong></p>
          </section>
          <section v-if="timingStages.length" class="paper-stage-list">
            <div class="paper-analysis-heading"><Activity :size="17" /><h2>阶段耗时</h2><span>{{ output.timing?.total_seconds?.toFixed(2) }}s</span></div>
            <p v-for="([name, seconds]) in timingStages" :key="name"><span>{{ name }}</span><strong>{{ seconds.toFixed(3) }}s</strong></p>
          </section>
          <section class="paper-reliability-list">
            <article v-for="item in reliabilityRecords" :key="`${item.item_type}-${item.item_id}`">
              <div><span class="status-tag">{{ item.status }}</span><small>{{ item.item_type }} · {{ item.source }}</small></div>
              <p>{{ item.final_content || item.review_candidate_content || item.reason }}</p>
              <small>{{ item.reason }}</small>
            </article>
          </section>
          <section v-if="report?.flow_execution?.degradations.length" class="paper-degradation-list">
            <article v-for="item in report.flow_execution.degradations" :key="`${item.stage}-${item.code}`">
              <strong>{{ item.code }}</strong><span>{{ item.stage }}</span><p>{{ item.message }}</p>
            </article>
          </section>
        </div>

        <article v-else class="literature-report-view paper-reading-report">
          <div v-if="!output.report_markdown" class="literature-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><FileText v-else :size="24" /><strong>完整报告尚未生成</strong></div>
          <template v-for="(block, index) in markdownBlocks" :key="index">
            <h1 v-if="block.type === 'h1'">{{ block.text }}</h1><h2 v-else-if="block.type === 'h2'">{{ block.text }}</h2><h3 v-else-if="block.type === 'h3'">{{ block.text }}</h3><li v-else-if="block.type === 'li'">{{ block.text }}</li><p v-else-if="block.type === 'p'">{{ block.text }}</p><br v-else />
          </template>
        </article>
      </section>
    </div>
  </div>
</template>
