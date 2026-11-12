<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  Bot,
  CheckCircle2,
  Code2,
  Database,
  Download,
  FileImage,
  FileText,
  FolderOpen,
  Image,
  LoaderCircle,
  Palette,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import AgentPromptBox from '@/components/AgentPromptBox.vue'
import type { AcademicFigureQuality, ResearchTask } from '@/types'

type InputKind = 'data' | 'context' | 'sketch'
type ResultTab = 'preview' | 'spec' | 'data' | 'quality' | 'code' | 'files'

interface SelectedInput {
  id: number
  kind: InputKind
  file: File
}

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
const prompt = ref('根据上传数据生成适合论文投稿的图表，突出主要趋势、差异和不确定性，并给出中英文图注')
const figureType = ref('auto')
const planningMode = ref<'online' | 'offline'>('online')
const exportFormats = ref(['png', 'svg', 'pdf'])
const codeFormats = ref(['python', 'r', 'latex', 'mermaid'])
const languages = ref(['zh', 'en'])
const selectedInputs = ref<SelectedInput[]>([])
const task = ref<ResearchTask | null>(null)
const events = ref<TaskEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const activeTab = ref<ResultTab>('preview')
const previewLanguage = ref<'zh' | 'en'>('zh')
const activeCode = ref('python')
const codeText = ref('')
const codeLoading = ref(false)
const dataInput = ref<HTMLInputElement | null>(null)
const contextInput = ref<HTMLInputElement | null>(null)
const sketchInput = ref<HTMLInputElement | null>(null)
let nextInputId = 1
let closeEvents: (() => void) | null = null

const output = computed(() => task.value?.output ?? {})
const figureRequest = computed(() => output.value.figure_request ?? {})
const spec = computed(() => output.value.figure_spec ?? {})
const dataset = computed(() => output.value.dataset_summary ?? {})
const quality = computed<AcademicFigureQuality>(() => output.value.figure_quality ?? {})
const warnings = computed(() => output.value.figure_warnings ?? [])
const isOfflineRun = computed(() => figureRequest.value.offline === true)
const artifacts = computed(() => output.value.artifacts ?? {})
const isRunning = computed(() => task.value != null && !['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status))
const previewArtifact = computed(() => {
  const preferred = `figure-${previewLanguage.value}-png`
  if (preferred in artifacts.value) return preferred
  return Object.keys(artifacts.value).find((key) => key.endsWith('-png')) || ''
})
const previewUrl = computed(() => task.value && previewArtifact.value
  ? artifactUrl(previewArtifact.value)
  : '')
const qualityChecks = computed(() => quality.value.checks ?? [])
const availableCode = computed(() => codeFormats.value.filter((format) => `figure-code-${format}` in artifacts.value))
const availableArtifacts = computed(() => Object.keys(artifacts.value).map((kind) => ({
  kind,
  label: artifactLabels[kind] || kind,
})))

const figureTypes = [
  { value: 'auto', label: '自动判断' },
  { value: 'line', label: '折线图' },
  { value: 'bar', label: '柱状图' },
  { value: 'scatter', label: '散点图' },
  { value: 'box', label: '箱线图' },
  { value: 'heatmap', label: '热力图' },
  { value: 'flowchart', label: '流程图' },
  { value: 'image_panel', label: '图片拼版' },
]

const artifactLabels: Record<string, string> = {
  'figure-zh-png': '中文图表 PNG',
  'figure-zh-svg': '中文图表 SVG',
  'figure-zh-pdf': '中文图表 PDF',
  'figure-en-png': '英文图表 PNG',
  'figure-en-svg': '英文图表 SVG',
  'figure-en-pdf': '英文图表 PDF',
  'figure-code-python': 'Python 复现代码',
  'figure-code-r': 'R 复现代码',
  'figure-code-latex': 'LaTeX 代码',
  'figure-code-mermaid': 'Mermaid 代码',
  'figure-caption-zh': '中文图注',
  'figure-caption-en': '英文图注',
  'figure-source-data': '规范化数据 CSV',
  'figure-config': 'FigureSpec 配置',
  'figure-quality': '质量报告',
  'figure-execution': '执行记录',
  'figure-manifest': '产物清单',
  'figure-request': '任务请求',
}

function chooseInput(kind: InputKind) {
  ({ data: dataInput, context: contextInput, sketch: sketchInput }[kind]).value?.click()
}

function addFiles(event: Event, kind: InputKind) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  const remaining = Math.max(0, 12 - selectedInputs.value.length)
  selectedInputs.value.push(...files.slice(0, remaining).map((file) => ({ id: nextInputId++, kind, file })))
  if (files.length > remaining) errorMessage.value = '单个任务最多上传 12 个文件'
  input.value = ''
}

function removeInput(id: number) {
  selectedInputs.value = selectedInputs.value.filter((item) => item.id !== id)
}

function groupedInputs(kind: InputKind) {
  return selectedInputs.value.filter((item) => item.kind === kind)
}

function validateInputs() {
  const kinds = new Set(selectedInputs.value.map((item) => item.kind))
  if (['line', 'bar', 'scatter', 'box', 'heatmap'].includes(figureType.value) && !kinds.has('data')) {
    return '统计图需要至少上传一个数据文件'
  }
  if (figureType.value === 'image_panel' && !kinds.has('sketch')) {
    return '图片拼版需要至少上传一张草图或实验图片'
  }
  if (!exportFormats.value.length || !languages.value.length) return '请至少选择一种输出格式和一种语言'
  return ''
}

async function startTask(payload: AgentPromptPayload) {
  if (busy.value) return
  const validationError = validateInputs()
  if (validationError) {
    errorMessage.value = validationError
    return
  }
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const uploaded = []
    for (const item of selectedInputs.value) {
      const form = new FormData()
      form.append('kind', item.kind)
      form.append('file', item.file)
      const response = await http.post<{ data: { uploadId: string; fileName: string; kind: InputKind } }>('/uploads/figures', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      uploaded.push({
        upload_id: response.data.data.uploadId,
        file_name: response.data.data.fileName,
        kind: response.data.data.kind,
      })
    }
    const modelConfigId = payload.model.startsWith('model_config:')
      ? payload.model.slice('model_config:'.length)
      : null
    const response = await http.post('/tasks', {
      prompt: payload.prompt,
      agent_code: 'academic_figure',
      model: payload.model,
      model_config_id: modelConfigId,
      figure_type: figureType.value,
      figure_planning_mode: planningMode.value,
      figure_export_formats: exportFormats.value,
      figure_code_formats: codeFormats.value,
      figure_languages: languages.value,
      figure_files: uploaded,
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
    prompt.value = task.value.prompt
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
    'task.started', 'figure.sources_ready', 'figure.planning', 'figure.rendered',
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

function artifactUrl(kind: string) {
  return task.value ? apiUrl(`/tasks/${task.value.id}/artifacts/${kind}`) : '#'
}

async function loadCode(format = activeCode.value) {
  activeCode.value = format
  const kind = `figure-code-${format}`
  if (!task.value || !(kind in artifacts.value)) {
    codeText.value = ''
    return
  }
  codeLoading.value = true
  try {
    const response = await http.get(artifactUrl(kind), { responseType: 'text' })
    codeText.value = String(response.data)
  } catch (error) {
    codeText.value = requestError(error)
  } finally {
    codeLoading.value = false
  }
}

function resetWorkspace() {
  closeEvents?.()
  closeEvents = null
  task.value = null
  events.value = []
  codeText.value = ''
  errorMessage.value = ''
  void router.replace({ path: route.path })
}

function formatValue(value: unknown) {
  if (value == null || value === '') return '-'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
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

watch(activeTab, (value) => {
  if (value === 'code') void loadCode(availableCode.value[0] || activeCode.value)
})

watch(availableCode, (values) => {
  if (activeTab.value === 'code' && values.length && !values.includes(activeCode.value)) void loadCode(values[0])
})

onBeforeUnmount(() => closeEvents?.())
</script>

<template>
  <div class="figure-agent-view">
    <header class="figure-agent-header">
      <div><span class="figure-agent-mark"><Palette :size="18" /></span><span><strong>绘图创作</strong><small>Academic Figure Agent · 数据、上下文与草图驱动的可复现学术图表</small></span></div>
      <button v-if="task" class="secondary-button" type="button" @click="resetWorkspace">创建新图</button>
    </header>

    <section v-if="!task" class="figure-agent-empty">
      <div class="figure-agent-intro">
        <span><BarChart3 :size="26" /></span>
        <p class="eyebrow">ACADEMIC FIGURE AGENT</p>
        <h1>把实验数据变成可投稿、可复现的学术图表</h1>
        <p>组合数据、论文上下文和草图，生成双语图表、图注、FigureSpec、规范化数据与多语言复现代码。</p>
      </div>

      <div class="figure-config-grid">
        <label><span>图表类型</span><select v-model="figureType"><option v-for="item in figureTypes" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <label><span>规划模式</span><select v-model="planningMode"><option value="online">模型规划</option><option value="offline">离线确定性规划</option></select></label>
        <fieldset><legend>输出语言</legend><label><input v-model="languages" type="checkbox" value="zh" />中文</label><label><input v-model="languages" type="checkbox" value="en" />英文</label></fieldset>
        <fieldset><legend>图形格式</legend><label><input v-model="exportFormats" type="checkbox" value="png" />PNG</label><label><input v-model="exportFormats" type="checkbox" value="svg" />SVG</label><label><input v-model="exportFormats" type="checkbox" value="pdf" />PDF</label></fieldset>
      </div>

      <div class="figure-upload-grid">
        <section>
          <div><Database :size="17" /><strong>数据文件</strong><small>CSV / TSV / XLS / XLSX / JSON / JSONL</small></div>
          <button type="button" @click="chooseInput('data')"><FolderOpen :size="15" />添加数据</button>
          <input ref="dataInput" class="sr-only" type="file" multiple accept=".csv,.tsv,.xls,.xlsx,.json,.jsonl" @change="addFiles($event, 'data')" />
          <ul><li v-for="item in groupedInputs('data')" :key="item.id"><FileText :size="14" /><span>{{ item.file.name }}</span><button type="button" title="移除" aria-label="移除文件" @click="removeInput(item.id)"><X :size="13" /></button></li></ul>
        </section>
        <section>
          <div><FileText :size="17" /><strong>论文上下文</strong><small>PDF / DOCX / TXT / MD / TEX</small></div>
          <button type="button" @click="chooseInput('context')"><FolderOpen :size="15" />添加上下文</button>
          <input ref="contextInput" class="sr-only" type="file" multiple accept=".pdf,.docx,.txt,.md,.tex" @change="addFiles($event, 'context')" />
          <ul><li v-for="item in groupedInputs('context')" :key="item.id"><FileText :size="14" /><span>{{ item.file.name }}</span><button type="button" title="移除" aria-label="移除文件" @click="removeInput(item.id)"><X :size="13" /></button></li></ul>
        </section>
        <section>
          <div><Image :size="17" /><strong>草图与实验图片</strong><small>PNG / JPG / JPEG / WEBP / BMP / TIFF</small></div>
          <button type="button" @click="chooseInput('sketch')"><FolderOpen :size="15" />添加图片</button>
          <input ref="sketchInput" class="sr-only" type="file" multiple accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff" @change="addFiles($event, 'sketch')" />
          <ul><li v-for="item in groupedInputs('sketch')" :key="item.id"><FileImage :size="14" /><span>{{ item.file.name }}</span><button type="button" title="移除" aria-label="移除文件" @click="removeInput(item.id)"><X :size="13" /></button></li></ul>
        </section>
      </div>

      <div class="figure-code-options"><span>复现代码</span><label><input v-model="codeFormats" type="checkbox" value="python" disabled />Python</label><label><input v-model="codeFormats" type="checkbox" value="r" />R</label><label><input v-model="codeFormats" type="checkbox" value="latex" />LaTeX</label><label><input v-model="codeFormats" type="checkbox" value="mermaid" />Mermaid</label><small v-if="planningMode === 'offline'">当前使用离线规则规划，不调用模型。</small></div>

      <AgentPromptBox
        v-model="prompt"
        :busy="busy"
        :show-file-picker="false"
        :allow-personal-models="planningMode === 'online'"
        :show-model-selector="planningMode === 'online'"
        placeholder="描述图表目的、比较关系、颜色偏好、期刊规范或需要强调的结论"
        hint="最多 12 个文件 · 运行时与产物均已合并到 Web 系统"
        @submit="startTask"
      />
      <p v-if="errorMessage" class="figure-error">{{ errorMessage }}</p>
    </section>

    <div v-else class="figure-workspace">
      <aside class="figure-trace-pane">
        <div class="figure-task-heading"><span>绘图任务</span><strong>{{ task.progress }}%</strong><h1>{{ task.prompt }}</h1><div><i :style="{ width: `${task.progress}%` }"></i></div></div>
        <dl class="figure-run-summary"><div><dt>图表类型</dt><dd>{{ spec.figure_type || '规划中' }}</dd></div><div><dt>规划模式</dt><dd>{{ isOfflineRun ? '离线规则' : '模型规划' }}</dd></div><div><dt>数据行数</dt><dd>{{ dataset.row_count ?? '-' }}</dd></div><div><dt>质量状态</dt><dd>{{ quality.passed == null ? '检查中' : quality.passed ? '通过' : '需复核' }}</dd></div></dl>
        <div class="figure-event-log"><div><Bot :size="15" />Agent 进度</div><ol><li v-for="event in events" :key="event.sequence"><i></i><span>{{ event.message }}</span><small>{{ event.progress }}%</small></li><li v-if="events.length === 0"><i></i><span>{{ task.current_step || '正在恢复任务状态' }}</span><small>{{ task.progress }}%</small></li></ol></div>
        <p v-if="isOfflineRun" class="figure-offline-notice">离线结果由确定性规则生成，不代表模型规划。</p>
      </aside>

      <main class="figure-result-pane">
        <nav class="figure-result-tabs" aria-label="绘图结果视图">
          <button type="button" :class="{ active: activeTab === 'preview' }" @click="activeTab = 'preview'"><Image :size="15" />预览</button>
          <button type="button" :class="{ active: activeTab === 'spec' }" @click="activeTab = 'spec'"><Sparkles :size="15" />FigureSpec</button>
          <button type="button" :class="{ active: activeTab === 'data' }" @click="activeTab = 'data'"><Database :size="15" />数据</button>
          <button type="button" :class="{ active: activeTab === 'quality' }" @click="activeTab = 'quality'"><ShieldCheck :size="15" />质量<span>{{ qualityChecks.length }}</span></button>
          <button type="button" :class="{ active: activeTab === 'code' }" @click="activeTab = 'code'"><Code2 :size="15" />代码</button>
          <button type="button" :class="{ active: activeTab === 'files' }" @click="activeTab = 'files'"><Download :size="15" />文件<span>{{ availableArtifacts.length }}</span></button>
        </nav>

        <section v-if="activeTab === 'preview'" class="figure-preview-pane">
          <div class="figure-preview-toolbar"><div><button type="button" :class="{ active: previewLanguage === 'zh' }" @click="previewLanguage = 'zh'">中文</button><button type="button" :class="{ active: previewLanguage === 'en' }" @click="previewLanguage = 'en'">English</button></div><a v-if="previewArtifact" :href="artifactUrl(previewArtifact)"><Download :size="14" />下载当前图片</a></div>
          <div v-if="previewUrl" class="figure-canvas"><img :src="previewUrl" alt="学术图表生成结果" /></div>
          <div v-else class="figure-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><Image v-else :size="24" /><strong>{{ isRunning ? '正在生成图表' : '没有可预览的 PNG 产物' }}</strong><span>{{ task.error || task.current_step }}</span></div>
          <div v-if="output.figure_captions?.[previewLanguage]" class="figure-caption"><strong>{{ previewLanguage === 'zh' ? '中文图注' : 'English caption' }}</strong><p>{{ output.figure_captions[previewLanguage] }}</p></div>
        </section>

        <section v-else-if="activeTab === 'spec'" class="figure-spec-pane">
          <div class="figure-spec-grid"><div><span>标题</span><strong>{{ spec.title?.zh || spec.title?.en || '-' }}</strong></div><div><span>视觉类型</span><strong>{{ spec.figure_type || '-' }}</strong></div><div><span>X 变量</span><strong>{{ spec.x || '-' }}</strong></div><div><span>Y 变量</span><strong>{{ spec.y || '-' }}</strong></div><div><span>系列变量</span><strong>{{ spec.series || '-' }}</strong></div><div><span>分辨率</span><strong>{{ spec.dpi ? `${spec.dpi} DPI` : '-' }}</strong></div></div>
          <div v-if="spec.palette?.length" class="figure-palette"><span>颜色方案</span><i v-for="color in spec.palette" :key="color" :style="{ background: color }" :title="color"></i></div>
          <section v-if="spec.assumptions?.length"><h2>规划假设</h2><p v-for="item in spec.assumptions" :key="item">{{ item }}</p></section>
          <pre>{{ JSON.stringify(spec, null, 2) }}</pre>
        </section>

        <section v-else-if="activeTab === 'data'" class="figure-data-pane">
          <div class="figure-data-metrics"><div><strong>{{ dataset.row_count ?? 0 }}</strong><span>数据行</span></div><div><strong>{{ dataset.columns?.length ?? 0 }}</strong><span>字段</span></div><div><strong>{{ dataset.numeric_columns?.length ?? 0 }}</strong><span>数值字段</span></div><div><strong>{{ Object.values(dataset.missing_values ?? {}).reduce((sum, value) => sum + value, 0) }}</strong><span>缺失值</span></div></div>
          <div v-if="dataset.preview?.length" class="figure-data-table"><table><thead><tr><th v-for="column in dataset.columns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in dataset.preview" :key="index"><td v-for="column in dataset.columns" :key="column">{{ formatValue(row[column]) }}</td></tr></tbody></table></div>
          <div v-else class="figure-result-empty"><Database :size="24" /><strong>当前任务没有结构化数据预览</strong><span>流程图或图片拼版可以只使用上下文与图片。</span></div>
        </section>

        <section v-else-if="activeTab === 'quality'" class="figure-quality-pane">
          <div class="figure-quality-summary" :class="{ passed: quality.passed }"><CheckCircle2 v-if="quality.passed" :size="22" /><ShieldCheck v-else :size="22" /><div><strong>{{ quality.passed ? '图表质量检查通过' : isRunning ? '质量检查尚未完成' : '存在需要复核的检查项' }}</strong><span>修订轮次 {{ quality.revision ?? 0 }} · {{ qualityChecks.length }} 项检查</span></div></div>
          <div class="figure-check-list"><article v-for="check in qualityChecks" :key="check.name" :class="check.status"><span>{{ check.status }}</span><div><strong>{{ check.name }}</strong><p>{{ check.message }}</p></div></article></div>
          <section v-if="warnings.length"><h2>运行提示</h2><p v-for="warning in warnings" :key="warning">{{ warning }}</p></section>
        </section>

        <section v-else-if="activeTab === 'code'" class="figure-code-pane">
          <div class="figure-code-tabs"><button v-for="format in availableCode" :key="format" type="button" :class="{ active: activeCode === format }" @click="loadCode(format)">{{ format }}</button><a v-if="activeCode" :href="artifactUrl(`figure-code-${activeCode}`)"><Download :size="14" />下载</a></div>
          <div v-if="codeLoading" class="figure-result-empty"><LoaderCircle class="spin" :size="24" /><strong>正在读取代码</strong></div><pre v-else>{{ codeText || '当前任务没有可用的复现代码。' }}</pre>
        </section>

        <section v-else class="figure-files-pane">
          <a v-for="item in availableArtifacts" :key="item.kind" :href="artifactUrl(item.kind)"><span><FileText :size="17" /><strong>{{ item.label }}</strong></span><Download :size="16" /></a>
          <div v-if="!availableArtifacts.length" class="figure-result-empty"><LoaderCircle v-if="isRunning" class="spin" :size="24" /><Download v-else :size="24" /><strong>{{ isRunning ? '正在整理交付文件' : '没有可下载文件' }}</strong></div>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.figure-agent-view{min-height:calc(100vh - 36px);background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}.figure-agent-header{min-height:64px;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:#fff}.figure-agent-header>div{display:flex;align-items:center;gap:11px}.figure-agent-header strong,.figure-agent-header small{display:block}.figure-agent-header strong{font-size:16px}.figure-agent-header small{margin-top:2px;color:var(--muted);font-size:13px}.figure-agent-mark{width:34px;height:34px;display:grid;place-items:center;color:#fff;background:var(--green-900);border-radius:6px}.figure-agent-empty{width:min(1120px,calc(100% - 40px));margin:0 auto;padding:34px 0 44px;display:grid;gap:20px}.figure-agent-intro{text-align:center}.figure-agent-intro>span{width:48px;height:48px;margin:0 auto 12px;display:grid;place-items:center;color:var(--green-900);background:var(--green-100);border-radius:8px}.figure-agent-intro h1{margin:6px 0 8px;font-size:27px}.figure-agent-intro>p:last-child{max-width:690px;margin:0 auto;color:var(--muted)}.figure-config-grid{display:grid;grid-template-columns:repeat(2,minmax(180px,1fr)) 1.2fr 1.4fr;gap:12px;align-items:end}.figure-config-grid>label,.figure-config-grid fieldset{min-height:72px;margin:0;padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface-soft)}.figure-config-grid>label{display:grid;gap:5px}.figure-config-grid span,.figure-config-grid legend{color:var(--muted);font-size:14px;font-weight:700}.figure-config-grid select{width:100%;border:0;background:transparent;outline:0}.figure-config-grid fieldset{display:flex;align-items:center;gap:12px}.figure-config-grid legend{padding:0 4px}.figure-config-grid fieldset label,.figure-code-options label{display:flex;align-items:center;gap:5px;white-space:nowrap}.figure-upload-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.figure-upload-grid>section{min-height:160px;padding:15px;border:1px solid var(--line);border-radius:6px;background:#fff}.figure-upload-grid section>div{display:grid;grid-template-columns:auto 1fr;column-gap:8px;align-items:center}.figure-upload-grid section>div svg{grid-row:1/3;color:var(--green-700)}.figure-upload-grid section>div small{color:var(--muted);font-size:12px}.figure-upload-grid section>button{margin:12px 0 7px;padding:7px 10px;display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:5px;background:var(--surface-soft)}.figure-upload-grid ul{margin:0;padding:0;display:grid;gap:5px;list-style:none}.figure-upload-grid li{min-width:0;padding:5px 7px;display:flex;align-items:center;gap:6px;background:var(--green-100);border-radius:4px}.figure-upload-grid li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.figure-upload-grid li button{margin-left:auto;padding:2px;display:grid;place-items:center;background:transparent}.figure-code-options{display:flex;align-items:center;gap:14px;min-height:40px;color:var(--muted)}.figure-code-options>span{font-weight:700}.figure-code-options small{margin-left:auto;color:var(--warning)}.figure-error{margin:0;color:var(--danger)}.figure-workspace{min-height:calc(100vh - 101px);display:grid;grid-template-columns:300px minmax(0,1fr)}.figure-trace-pane{padding:22px 18px;border-right:1px solid var(--line);background:var(--surface-soft);overflow:auto}.figure-task-heading>span{color:var(--green-700);font-size:13px;font-weight:800;text-transform:uppercase}.figure-task-heading>strong{float:right}.figure-task-heading h1{margin:10px 0 14px;font-size:17px;line-height:1.45}.figure-task-heading>div{height:5px;background:var(--line);overflow:hidden;border-radius:3px}.figure-task-heading i{height:100%;display:block;background:var(--green-700)}.figure-run-summary{margin:20px 0;display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--line);border-left:1px solid var(--line)}.figure-run-summary div{padding:10px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.figure-run-summary dt{color:var(--muted);font-size:12px}.figure-run-summary dd{margin:3px 0 0;font-weight:700}.figure-event-log>div{display:flex;align-items:center;gap:6px;font-weight:700}.figure-event-log ol{margin:12px 0;padding:0;display:grid;gap:12px;list-style:none}.figure-event-log li{display:grid;grid-template-columns:10px 1fr auto;gap:7px;align-items:start}.figure-event-log li i{width:7px;height:7px;margin-top:6px;background:var(--green-700);border-radius:50%}.figure-event-log li small{color:var(--muted)}.figure-offline-notice{padding:10px;border-left:3px solid var(--warning);color:var(--muted);background:#fff9ed}.figure-result-pane{min-width:0;display:grid;grid-template-rows:auto minmax(0,1fr);background:#fff}.figure-result-tabs{padding:0 16px;display:flex;align-items:center;gap:3px;border-bottom:1px solid var(--line);overflow-x:auto}.figure-result-tabs button{min-height:52px;padding:0 12px;display:flex;align-items:center;gap:6px;border-bottom:2px solid transparent;background:transparent;white-space:nowrap}.figure-result-tabs button.active{color:var(--green-900);border-bottom-color:var(--green-700);font-weight:700}.figure-result-tabs button span{min-width:18px;padding:1px 5px;background:var(--green-100);border-radius:9px;font-size:12px}.figure-preview-pane,.figure-spec-pane,.figure-data-pane,.figure-quality-pane,.figure-code-pane,.figure-files-pane{min-height:0;padding:22px;overflow:auto}.figure-preview-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.figure-preview-toolbar>div{display:flex;border:1px solid var(--line);border-radius:5px;overflow:hidden}.figure-preview-toolbar button{padding:6px 11px;background:#fff}.figure-preview-toolbar button.active{background:var(--green-900);color:#fff}.figure-preview-toolbar a,.figure-code-tabs a{display:flex;align-items:center;gap:6px;color:var(--green-700);font-weight:700}.figure-canvas{min-height:420px;padding:16px;display:grid;place-items:center;border:1px solid var(--line);background:#f8faf9}.figure-canvas img{display:block;max-width:100%;max-height:68vh;object-fit:contain}.figure-caption{padding:16px 0;border-bottom:1px solid var(--line)}.figure-caption p{margin:6px 0 0;color:var(--muted);line-height:1.75}.figure-result-empty{min-height:300px;display:grid;place-items:center;align-content:center;gap:8px;color:var(--muted);text-align:center}.figure-spec-grid,.figure-data-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--line);border-left:1px solid var(--line)}.figure-spec-grid>div,.figure-data-metrics>div{padding:14px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.figure-spec-grid span,.figure-data-metrics span{display:block;color:var(--muted);font-size:13px}.figure-spec-pane pre,.figure-code-pane pre{padding:16px;overflow:auto;border:1px solid var(--line);background:#17211c;color:#e8efeb;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap}.figure-palette{margin:18px 0;display:flex;align-items:center;gap:8px}.figure-palette i{width:28px;height:28px;border:1px solid var(--line);border-radius:4px}.figure-spec-pane h2,.figure-quality-pane h2{font-size:16px}.figure-data-metrics{grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:18px}.figure-data-metrics strong{display:block;font-size:22px}.figure-data-table{overflow:auto;border:1px solid var(--line)}.figure-data-table table{width:100%;border-collapse:collapse;font-size:14px}.figure-data-table th,.figure-data-table td{padding:9px 11px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}.figure-data-table th{background:var(--surface-soft)}.figure-quality-summary{padding:16px;display:flex;align-items:center;gap:12px;border-left:4px solid var(--warning);background:#fff9ed}.figure-quality-summary.passed{border-left-color:var(--success);background:var(--green-100)}.figure-quality-summary strong,.figure-quality-summary span{display:block}.figure-quality-summary span{color:var(--muted)}.figure-check-list{margin:16px 0;display:grid;gap:8px}.figure-check-list article{padding:12px 0;display:grid;grid-template-columns:70px 1fr;gap:12px;border-bottom:1px solid var(--line)}.figure-check-list article>span{align-self:start;padding:3px 7px;text-align:center;border-radius:4px;background:#eef1ef;font-size:12px;text-transform:uppercase}.figure-check-list article.passed>span{color:var(--success);background:var(--green-100)}.figure-check-list article.failed>span{color:var(--danger);background:#fff0ee}.figure-check-list p{margin:4px 0 0;color:var(--muted)}.figure-code-tabs{display:flex;align-items:center;gap:5px;margin-bottom:12px}.figure-code-tabs button{padding:6px 11px;border:1px solid var(--line);background:#fff;border-radius:4px;text-transform:uppercase}.figure-code-tabs button.active{color:#fff;background:var(--green-900);border-color:var(--green-900)}.figure-code-tabs a{margin-left:auto}.figure-code-pane pre{min-height:430px;margin:0}.figure-files-pane{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;align-content:start}.figure-files-pane>a{min-height:58px;padding:12px;display:flex;align-items:center;justify-content:space-between;border:1px solid var(--line);border-radius:6px}.figure-files-pane>a:hover{border-color:var(--green-700);background:var(--green-100)}.figure-files-pane>a span{display:flex;align-items:center;gap:8px}.secondary-button{padding:8px 12px;border:1px solid var(--line);border-radius:5px;background:#fff}.spin{animation:figure-spin .9s linear infinite}@keyframes figure-spin{to{transform:rotate(360deg)}}
@media(max-width:1050px){.figure-config-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.figure-upload-grid{grid-template-columns:1fr}.figure-workspace{grid-template-columns:250px minmax(0,1fr)}}
@media(max-width:760px){.figure-agent-view{border-left:0;border-right:0;border-radius:0}.figure-agent-header small{display:none}.figure-agent-empty{width:calc(100% - 28px);padding-top:24px}.figure-agent-intro h1{font-size:22px}.figure-config-grid{grid-template-columns:1fr}.figure-code-options{flex-wrap:wrap}.figure-code-options small{width:100%;margin-left:0}.figure-workspace{display:block}.figure-trace-pane{border-right:0;border-bottom:1px solid var(--line)}.figure-result-pane{min-height:600px}.figure-result-tabs{padding:0 8px}.figure-preview-pane,.figure-spec-pane,.figure-data-pane,.figure-quality-pane,.figure-code-pane,.figure-files-pane{padding:14px}.figure-canvas{min-height:280px}.figure-spec-grid,.figure-data-metrics,.figure-files-pane{grid-template-columns:1fr 1fr}.figure-preview-toolbar{align-items:flex-start;gap:10px}.figure-preview-toolbar>a{font-size:14px}}
@media(max-width:480px){.figure-spec-grid,.figure-data-metrics,.figure-files-pane{grid-template-columns:1fr}.figure-config-grid fieldset{flex-wrap:wrap}.figure-result-tabs button{padding:0 9px}.figure-agent-header{padding:10px 14px}.figure-agent-header>div>span:last-child small{display:none}}
/* Keep the preview and caption reachable inside the scrollable result pane. */
.figure-preview-pane{padding-bottom:78px;scroll-padding-bottom:78px}
.figure-caption{margin-bottom:24px;padding-bottom:28px}
</style>
