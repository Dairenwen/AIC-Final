<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Download,
  FileCheck2,
  FileText,
  Languages,
  LibraryBig,
  LoaderCircle,
  ScanText,
  ShieldCheck,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type {
  ResearchTask,
  TranslationFile,
  TranslationQuality,
  TranslationRequest,
  TranslationSegment,
  TranslationTaskEvent,
  TranslationTerm,
} from '@/types'

import {
  buildTranslationFormState,
  collectTranslationQualityIssues,
  collectTranslationWarnings,
  deriveTranslationStatusSummary,
  getTranslationEventMeta,
  getTranslationEventTone,
  parseTranslationGlossary,
  selectTranslationPreviewState,
  sortTranslationFiles,
  validateTranslationSubmission,
} from './academicTranslation'

interface AgentPromptPayload {
  prompt: string
  model: string
  attachment: string | null
  link: string | null
  file: File | null
}

const route = useRoute()
const router = useRouter()

const query = ref('将这篇学术文档翻译为中文，保持术语一致并保护公式、引用、数值和方法名')
const sourceLang = ref('en')
const targetLang = ref('zh')
const precision = ref<'reading' | 'submission'>('reading')
const glossaryText = ref('{}')
const bilingual = ref(false)
const preserveLayout = ref(false)
const translateFigures = ref(false)
const parallel = ref(2)
const task = ref<ResearchTask | null>(null)
const events = ref<TranslationTaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const validationMessages = ref<string[]>([])
const activeTab = ref<'preview' | 'quality' | 'glossary' | 'files'>('preview')
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const request = computed<TranslationRequest | undefined>(() => output.value.translation_request)
const segments = computed<TranslationSegment[]>(() => output.value.translation_segments ?? [])
const terms = computed<TranslationTerm[]>(() => output.value.translation_glossary ?? [])
const files = computed<TranslationFile[]>(() => output.value.translation_files ?? [])
const quality = computed<TranslationQuality>(() => output.value.translation_quality ?? {})
const qualityIssues = computed(() => collectTranslationQualityIssues(quality.value))
const warnings = computed(() => collectTranslationWarnings(quality.value, output.value.translation_warnings ?? []))
const reviewCount = computed(() => qualityIssues.value.length + warnings.value.length)
const statusSummary = computed(() => deriveTranslationStatusSummary(task.value, events.value))
const isRunning = computed(() => statusSummary.value.tone === 'running')
const previewState = computed(() => selectTranslationPreviewState(files.value, segments.value, request.value))
const previewUrl = computed(() => (previewState.value.file ? fileUrl(previewState.value.file) : ''))
const orderedFiles = computed(() => sortTranslationFiles(files.value, previewState.value.file))
const statusIcon = computed(() => {
  if (statusSummary.value.tone === 'failed') return CircleAlert
  if (statusSummary.value.tone === 'warning') return AlertTriangle
  if (statusSummary.value.tone === 'success') return CheckCircle2
  return LoaderCircle
})

const languages = [
  { code: 'en', name: '英语' },
  { code: 'zh', name: '简体中文' },
  { code: 'ja', name: '日语' },
  { code: 'de', name: '德语' },
  { code: 'fr', name: '法语' },
  { code: 'es', name: '西班牙语' },
]

async function startTask(payload: AgentPromptPayload) {
  if (busy.value) return

  const validation = validateTranslationSubmission({
    file: payload.file,
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    preserveLayout: preserveLayout.value,
    glossaryText: glossaryText.value,
    parallel: parallel.value,
  })
  validationMessages.value = validation.messages
  if (validation.messages.length > 0) {
    errorMessage.value = ''
    return
  }

  const glossary = validation.glossary ?? parseTranslationGlossary(glossaryText.value)

  busy.value = true
  errorMessage.value = ''
  validationMessages.value = []
  events.value = []
  try {
    const formData = new FormData()
    formData.append('file', payload.file as File)
    const upload = await http.post<{ data: { uploadId: string; fileName: string } }>('/uploads/translations', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const response = await http.post('/tasks', {
      prompt: payload.prompt,
      agent_code: 'academic_translation',
      attachment: upload.data.data.fileName,
      attachment_id: upload.data.data.uploadId,
      model: 'translation-api',
      translation_source_lang: sourceLang.value,
      translation_target_lang: targetLang.value,
      translation_precision: precision.value,
      translation_glossary: glossary,
      translation_domain: 'academic',
      translation_parallel: parallel.value,
      translation_preserve_pdf_layout: preserveLayout.value,
      translation_bilingual: bilingual.value,
      translation_translate_figures: translateFigures.value,
      translation_pdf_layout_mode: 'batch',
      translation_pdf_timeout_seconds: 600,
    })
    task.value = response.data.data as ResearchTask
    hydrateFormFromTask(task.value)
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
  validationMessages.value = []
  try {
    const response = await http.get(`/tasks/${taskId}`)
    task.value = response.data.data as ResearchTask
    hydrateFormFromTask(task.value)
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
  if (['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) {
    closeEvents?.()
    closeEvents = null
  }
}

function hydrateFormFromTask(value: ResearchTask | null) {
  const restored = buildTranslationFormState(value)
  query.value = restored.query
  sourceLang.value = restored.sourceLang
  targetLang.value = restored.targetLang
  precision.value = restored.precision
  glossaryText.value = restored.glossaryText
  bilingual.value = restored.bilingual
  preserveLayout.value = restored.preserveLayout
  translateFigures.value = restored.translateFigures
  parallel.value = restored.parallel
}

function subscribe(taskId: string) {
  closeEvents?.()
  closeEvents = null
  if (task.value && ['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) return

  const source = new EventSource(apiUrl(`/tasks/${taskId}/events`))
  const eventTypes = [
    'task.started',
    'translation.source_ready',
    'translation.parsing',
    'translation.terms_ready',
    'translation.translating',
    'translation.heartbeat',
    'translation.quality_ready',
    'translation.exports_ready',
    'task.completed',
    'task.failed',
  ]
  const handle = (event: Event) => {
    const payload = JSON.parse((event as MessageEvent).data) as TranslationTaskEvent
    if (!events.value.some((item) => item.sequence === payload.sequence)) {
      events.value.push(payload)
    }
    if (task.value) {
      task.value.progress = payload.progress
      task.value.current_step = payload.message
      if (payload.type === 'task.failed') task.value.status = 'FAILED'
      if (payload.type === 'task.completed') task.value.status = 'SUCCEEDED'
    }
    void refreshTask()
    if (['task.completed', 'task.failed'].includes(payload.type)) {
      source.close()
      closeEvents = null
    }
  }
  eventTypes.forEach((eventType) => source.addEventListener(eventType, handle))
  source.onerror = () => {
    void refreshTask().finally(() => {
      if (task.value && ['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) {
        source.close()
        closeEvents = null
      }
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
  validationMessages.value = []
  activeTab.value = 'preview'
  void router.replace({ path: route.path })
}

function languageName(code?: string) {
  return languages.find((item) => item.code === code)?.name || code || '-'
}

function fileArtifactKind(kind: string) {
  return {
    pdf_monolingual: 'translation-pdf',
    pdf_bilingual: 'translation-bilingual-pdf',
    monolingual_markdown: 'translation-markdown',
    bilingual_markdown: 'translation-bilingual-markdown',
    monolingual_docx: 'translation-docx',
    translation_report: 'translation-report',
  }[kind]
}

function fileUrl(file: TranslationFile) {
  const artifact = fileArtifactKind(file.kind)
  return task.value && artifact ? apiUrl(`/tasks/${task.value.id}/artifacts/${artifact}`) : '#'
}

function isDownloadableFile(file: TranslationFile) {
  return Boolean(fileArtifactKind(file.kind))
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
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
  <div class="literature-agent-view translation-agent-view">
    <header class="literature-agent-header">
      <div>
        <span class="literature-agent-mark"><Languages :size="18" /></span>
        <span><strong>学术翻译</strong><small>Academic Translation Agent · 术语约束、元素保护与版式输出</small></span>
      </div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">翻译新文档</button>
    </header>

    <section v-if="!task" class="literature-agent-empty translation-agent-empty">
      <div class="literature-agent-intro">
        <span class="literature-agent-intro__icon"><ScanText :size="25" /></span>
        <p class="eyebrow">ACADEMIC TRANSLATION AGENT</p>
        <h1>保留学术语义与文档结构的专业翻译</h1>
        <p>上传论文或研究文档，以固定本地翻译模型执行术语一致性、公式引用保护和质量检查。</p>
      </div>

      <div class="translation-settings" aria-label="翻译设置">
        <label><span>源语言</span><select v-model="sourceLang"><option v-for="item in languages" :key="item.code" :value="item.code">{{ item.name }}</option></select></label>
        <span class="translation-direction" aria-hidden="true">→</span>
        <label><span>目标语言</span><select v-model="targetLang"><option v-for="item in languages" :key="item.code" :value="item.code">{{ item.name }}</option></select></label>
        <label><span>译文精度</span><select v-model="precision"><option value="reading">阅读级</option><option value="submission">投稿级润色</option></select></label>
        <label><span>并行数</span><select v-model.number="parallel"><option v-for="value in 5" :key="value" :value="value">{{ value }}</option></select></label>
      </div>

      <div class="translation-options-row">
        <label><input v-model="bilingual" type="checkbox" />双语对照</label>
        <label><input v-model="preserveLayout" type="checkbox" />保留 PDF 原版式</label>
        <label><input v-model="translateFigures" type="checkbox" />翻译安全图表标签</label>
        <span>模型来源：API 调度</span>
      </div>

      <label class="translation-glossary-input">
        <span>术语表 JSON</span>
        <textarea v-model="glossaryText" rows="4" spellcheck="false"></textarea>
      </label>

      <AgentPromptBox
        v-model="query"
        :busy="busy"
        :show-file-picker="true"
        :show-model-selector="false"
        accept=".md,.txt,.docx,.pdf,application/pdf"
        file-picker-label="上传文档"
        placeholder="说明翻译目标、术语偏好或需要重点保留的内容"
        hint="MD / TXT / DOCX / PDF · 本地固定学术翻译模型"
        @submit="startTask"
      />

      <ul v-if="validationMessages.length" class="translation-feedback-list translation-feedback-list--error">
        <li v-for="message in validationMessages" :key="message">{{ message }}</li>
      </ul>
      <p v-if="errorMessage" class="literature-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="literature-workspace translation-workspace">
      <section class="literature-trace-pane translation-trace-pane">
        <div class="literature-task-heading">
          <span>学术翻译任务</span><strong>{{ task.progress }}%</strong>
          <h1>{{ request?.file_name || task.prompt }}</h1>
          <div class="literature-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
        </div>

        <div class="translation-status-card" :class="`is-${statusSummary.tone}`">
          <div class="translation-status-card__icon">
            <component :is="statusIcon" :size="18" :class="{ spin: statusSummary.tone === 'running' }" />
          </div>
          <div class="translation-status-card__body">
            <strong>{{ statusSummary.label }}</strong>
            <p>{{ statusSummary.detail }}</p>
            <div class="translation-status-card__meta">
              <span><Clock3 :size="14" />耗时 {{ statusSummary.elapsedLabel }}</span>
              <span><Bot :size="14" />阶段：{{ statusSummary.stage }}</span>
              <span v-if="reviewCount"><ShieldCheck :size="14" />{{ reviewCount }} 项待查看</span>
            </div>
          </div>
        </div>

        <div class="translation-language-pair">
          <div><small>源语言</small><strong>{{ languageName(request?.source_lang) }}</strong></div>
          <Languages :size="18" />
          <div><small>目标语言</small><strong>{{ languageName(request?.target_lang) }}</strong></div>
        </div>

        <div class="literature-plan manuscript-plan-card translation-policy-card">
          <div class="literature-section-label"><ShieldCheck :size="15" />翻译配置</div>
          <ul>
            <li><CheckCircle2 :size="13" />{{ request?.precision === 'submission' ? '投稿级翻译与润色' : '阅读级准确翻译' }}</li>
            <li><CheckCircle2 :size="13" />{{ request?.bilingual ? '生成双语对照产物' : '生成单语译文产物' }}</li>
            <li><CheckCircle2 :size="13" />{{ request?.preserve_pdf_layout ? '保留原始 PDF 页面布局' : '不保留 PDF 原版式' }}</li>
            <li><CheckCircle2 :size="13" />{{ request?.translate_figures ? '翻译安全图表标签' : '保持图表标签原样' }}</li>
            <li><CheckCircle2 :size="13" />并行数 {{ request?.parallel ?? parallel }}</li>
            <li><CheckCircle2 :size="13" />术语表 {{ Object.keys((request?.glossary as Record<string, string> | undefined) ?? {}).length }} 项</li>
          </ul>
        </div>

        <div class="literature-event-log manuscript-event-log translation-event-log">
          <div class="literature-section-label"><Bot :size="15" />Agent 进度</div>
          <ol>
            <li
              v-for="event in events"
              :key="event.sequence"
              :class="`translation-event-row translation-event-row--${getTranslationEventTone(event)}`"
            >
              <i></i>
              <span>{{ event.message }}</span>
              <small>{{ getTranslationEventMeta(event) }}</small>
            </li>
            <li v-if="events.length === 0" class="translation-event-row translation-event-row--stage">
              <i></i>
              <span>{{ statusSummary.stage }}</span>
              <small>{{ statusSummary.elapsedLabel }}</small>
            </li>
          </ol>
        </div>
      </section>

      <section class="literature-result-pane translation-result-pane">
        <nav class="literature-result-tabs" aria-label="学术翻译结果视图">
          <button type="button" :class="{ active: activeTab === 'preview' }" @click="activeTab = 'preview'"><FileText :size="15" />译文预览</button>
          <button type="button" :class="{ active: activeTab === 'quality' }" @click="activeTab = 'quality'"><ShieldCheck :size="15" />质量检查<span>{{ reviewCount }}</span></button>
          <button type="button" :class="{ active: activeTab === 'glossary' }" @click="activeTab = 'glossary'"><LibraryBig :size="15" />术语表<span>{{ terms.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'files' }" @click="activeTab = 'files'"><Download :size="15" />文件<span>{{ orderedFiles.length }}</span></button>
        </nav>

        <div v-if="activeTab === 'preview'" class="translation-preview-pane">
          <div class="translation-preview-toolbar">
            <strong>{{ previewState.mode === 'pdf' ? 'PDF 预览' : previewState.mode === 'segments' ? '片段预览' : '预览状态' }}</strong>
            <span v-if="previewState.file">{{ previewState.file.label }} · {{ previewState.file.file_name }}</span>
            <span v-else-if="previewState.mode === 'segments'">当前没有可用 PDF，已回退为片段预览。</span>
            <span v-else>{{ isRunning ? '正在等待可预览结果' : '当前任务没有可预览产物' }}</span>
          </div>

          <iframe v-if="previewState.mode === 'pdf'" :src="previewUrl" :title="previewState.file?.label || '译文 PDF 预览'"></iframe>
          <div v-else-if="previewState.mode === 'segments'" class="translation-segment-list">
            <article v-for="segment in segments" :key="segment.segment_id" class="translation-segment">
              <div><span>{{ segment.kind || 'paragraph' }}</span><small v-if="segment.page">第 {{ segment.page }} 页</small></div>
              <p>{{ segment.source_text }}</p>
              <p>{{ segment.translated_text }}</p>
            </article>
          </div>
          <div v-else class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <Languages v-else :size="24" />
            <strong>{{ isRunning ? '正在生成译文预览' : '尚未生成可预览译文' }}</strong>
            <span>{{ statusSummary.detail }}</span>
          </div>
        </div>

        <div v-else-if="activeTab === 'quality'" class="translation-quality-pane">
          <div class="translation-quality-metrics">
            <div><strong>{{ quality.translated_segments ?? 0 }}</strong><span>已翻译片段</span></div>
            <div><strong>{{ quality.total_segments ?? 0 }}</strong><span>总片段</span></div>
            <div><strong>{{ qualityIssues.length }}</strong><span>待复核项</span></div>
            <div><strong>{{ warnings.length }}</strong><span>运行提示</span></div>
          </div>
          <section v-if="qualityIssues.length">
            <h2>待复核项</h2>
            <p v-for="(item, index) in qualityIssues" :key="`issue-${index}`"><ShieldCheck :size="14" />{{ item }}</p>
          </section>
          <section v-if="warnings.length">
            <h2>运行提示</h2>
            <p v-for="(item, index) in warnings" :key="`warning-${index}`">{{ item }}</p>
          </section>
          <div v-if="!qualityIssues.length && !warnings.length && task.status === 'SUCCEEDED'" class="translation-quality-passed">
            <CheckCircle2 :size="22" />
            <strong>译文质量检查通过</strong>
            <span>未发现术语、保护元素或格式违规。</span>
          </div>
          <div v-if="task.status === 'FAILED'" class="translation-quality-warning-box">
            <CircleAlert :size="18" />
            <div>
              <strong>任务未完成</strong>
              <p>{{ statusSummary.detail }}</p>
            </div>
          </div>
        </div>

        <div v-else-if="activeTab === 'glossary'" class="translation-glossary-pane">
          <table v-if="terms.length">
            <thead><tr><th>源术语</th><th>目标术语</th><th>来源</th><th>置信度</th></tr></thead>
            <tbody>
              <tr v-for="term in terms" :key="`${term.source}-${term.target}`">
                <td>{{ term.source }}</td>
                <td>{{ term.target }}</td>
                <td>{{ term.origin || '-' }}</td>
                <td>{{ term.confidence != null ? `${Math.round(term.confidence * 100)}%` : '-' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="literature-result-empty">
            <LibraryBig :size="24" />
            <strong>本次任务没有可展示术语</strong>
          </div>
        </div>

        <div v-else class="translation-files-pane">
          <p v-if="previewState.mode === 'segments'" class="translation-files-note">当前无 PDF 文件，预览已回退为片段视图；请从下方下载现有产物。</p>
          <template v-for="file in orderedFiles" :key="file.kind">
            <a
              v-if="isDownloadableFile(file)"
              :href="fileUrl(file)"
              class="translation-file-row"
              :class="{ 'is-preview': previewState.file?.kind === file.kind }"
            >
              <span>
                <FileCheck2 :size="19" />
                <span>
                  <strong>{{ file.label }}</strong>
                  <small>{{ file.file_name }} · {{ formatBytes(file.size) }}<template v-if="previewState.file?.kind === file.kind"> · 当前预览</template></small>
                </span>
              </span>
              <Download :size="17" />
            </a>
            <div v-else class="translation-file-row is-disabled">
              <span>
                <FileCheck2 :size="19" />
                <span>
                  <strong>{{ file.label }}</strong>
                  <small>{{ file.file_name }} · {{ formatBytes(file.size) }} · 当前前端不提供直链下载</small>
                </span>
              </span>
              <Download :size="17" />
            </div>
          </template>
          <div v-if="orderedFiles.length === 0" class="literature-result-empty">
            <LoaderCircle v-if="isRunning" class="spin" :size="24" />
            <Download v-else :size="24" />
            <strong>{{ isRunning ? '正在生成译文文件' : '尚无可下载文件' }}</strong>
            <span>{{ statusSummary.detail }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
