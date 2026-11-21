import type {
  ResearchTask,
  TranslationFile,
  TranslationQuality,
  TranslationRequest,
  TranslationSegment,
  TranslationTaskEvent,
} from '@/types'

export const SUPPORTED_TRANSLATION_EXTENSIONS = ['.md', '.txt', '.docx', '.pdf'] as const

const TERMINAL_TASK_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'CANCELED'])
const PDF_FILE_KINDS = ['pdf_monolingual', 'pdf_bilingual'] as const
const FILE_KIND_ORDER = [
  'pdf_monolingual',
  'pdf_bilingual',
  'monolingual_docx',
  'monolingual_markdown',
  'bilingual_markdown',
  'translation_report',
  'figure_translation_manifest',
  'table_translation_manifest',
] as const

export interface TranslationFormState {
  query: string
  sourceLang: string
  targetLang: string
  precision: 'reading' | 'submission'
  glossaryText: string
  bilingual: boolean
  preserveLayout: boolean
  translateFigures: boolean
  parallel: number
}

export interface TranslationSubmissionInput {
  file: Pick<File, 'name'> | null
  sourceLang: string
  targetLang: string
  preserveLayout: boolean
  glossaryText: string
  parallel: number
}

export interface TranslationValidationResult {
  messages: string[]
  glossary: Record<string, string> | null
  fileSuffix: string
}

export interface TranslationStatusSummary {
  tone: 'running' | 'success' | 'warning' | 'failed'
  label: string
  detail: string
  stage: string
  elapsedLabel: string
}

export interface TranslationPreviewState {
  mode: 'pdf' | 'segments' | 'empty'
  file: TranslationFile | null
}

export const DEFAULT_TRANSLATION_FORM_STATE: TranslationFormState = {
  query: '将这篇学术文档翻译为中文，保持术语一致并保护公式、引用、数值和方法名',
  sourceLang: 'en',
  targetLang: 'zh',
  precision: 'reading',
  glossaryText: '{}',
  bilingual: false,
  preserveLayout: false,
  translateFigures: false,
  parallel: 2,
}

export function parseTranslationGlossary(value: string): Record<string, string> {
  let parsed: unknown
  try {
    parsed = JSON.parse(value.trim() || '{}')
  } catch {
    throw new Error('术语表需要填写为合法 JSON 对象，例如 {"foundation model":"基础模型"}。')
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('术语表必须是 JSON 对象，格式为 {"源术语":"目标术语"}。')
  }
  const entries = Object.entries(parsed as Record<string, unknown>)
  if (entries.some(([source, target]) => !source.trim() || typeof target !== 'string' || !target.trim())) {
    throw new Error('术语表中的源术语和目标术语都必须是非空文本，请删除空键值后重试。')
  }
  return Object.fromEntries(entries.map(([source, target]) => [source.trim(), String(target).trim()]))
}

export function validateTranslationSubmission(input: TranslationSubmissionInput): TranslationValidationResult {
  const messages: string[] = []
  const fileSuffix = getFileSuffix(input.file?.name)

  if (!input.file) {
    messages.push('请先上传待翻译文档，支持 .md、.txt、.docx、.pdf。')
  } else if (!SUPPORTED_TRANSLATION_EXTENSIONS.includes(fileSuffix as (typeof SUPPORTED_TRANSLATION_EXTENSIONS)[number])) {
    messages.push('当前文件类型无法启动学术翻译，请改为上传 .md、.txt、.docx 或 .pdf 文件。')
  }

  if (input.sourceLang === input.targetLang) {
    messages.push('请将“源语言”和“目标语言”设置为不同语言，例如 English -> 简体中文。')
  }
  if (input.preserveLayout && fileSuffix && fileSuffix !== '.pdf') {
    messages.push('如需保留 PDF 原版式，请上传 PDF 文件，或关闭“保留 PDF 原版式”。')
  }
  if (!Number.isInteger(input.parallel) || input.parallel < 1 || input.parallel > 5) {
    messages.push('并行数需在 1 到 5 之间，请重新选择。')
  }

  let glossary: Record<string, string> | null = null
  try {
    glossary = parseTranslationGlossary(input.glossaryText)
  } catch (error) {
    messages.push((error as Error).message)
  }

  return { messages, glossary, fileSuffix }
}

export function buildTranslationFormState(task: ResearchTask | null): TranslationFormState {
  if (!task) return { ...DEFAULT_TRANSLATION_FORM_STATE }
  const request = task.output.translation_request
  return {
    query: task.prompt || DEFAULT_TRANSLATION_FORM_STATE.query,
    sourceLang: normalizeLanguageCode(request?.source_lang, DEFAULT_TRANSLATION_FORM_STATE.sourceLang),
    targetLang: normalizeLanguageCode(request?.target_lang, DEFAULT_TRANSLATION_FORM_STATE.targetLang),
    precision: request?.precision === 'submission' ? 'submission' : 'reading',
    glossaryText: stringifyGlossary(request?.glossary),
    bilingual: Boolean(request?.bilingual),
    preserveLayout: Boolean(request?.preserve_pdf_layout),
    translateFigures: Boolean(request?.translate_figures),
    parallel: normalizeParallel(request?.parallel),
  }
}

export function collectTranslationWarnings(
  quality: TranslationQuality | null | undefined,
  runtimeWarnings: string[] = [],
): string[] {
  return uniqueStrings([...(runtimeWarnings ?? []), ...(quality?.warnings ?? [])])
}

export function collectTranslationQualityIssues(quality: TranslationQuality | null | undefined): string[] {
  return uniqueStrings([
    ...(quality?.untranslated_segment_ids ?? []),
    ...(quality?.terminology_violations ?? []),
    ...(quality?.protected_token_violations ?? []),
    ...(quality?.format_violations ?? []),
  ])
}

export function deriveTranslationStatusSummary(
  task: ResearchTask | null,
  events: TranslationTaskEvent[],
  now = Date.now(),
): TranslationStatusSummary {
  if (!task) {
    return {
      tone: 'running',
      label: '等待提交',
      detail: '请上传文档并确认翻译配置。',
      stage: '尚未开始',
      elapsedLabel: '0秒',
    }
  }

  const warnings = collectTranslationWarnings(task.output.translation_quality, task.output.translation_warnings ?? [])
  const qualityIssues = collectTranslationQualityIssues(task.output.translation_quality)
  const latestHeartbeat = [...events].reverse().find((event) => event.type === 'translation.heartbeat')
  const latestFailure = [...events].reverse().find((event) => event.type === 'task.failed')
  const latestStage = [...events]
    .reverse()
    .find((event) => event.type !== 'translation.heartbeat' && event.type !== 'task.completed' && event.type !== 'task.failed')
  const stage = latestStage?.message || task.current_step || fallbackStage(task.status)
  const elapsedSeconds = latestHeartbeat?.elapsed_seconds ?? getTaskElapsedSeconds(task, now)
  const elapsedLabel = formatElapsedSeconds(elapsedSeconds)
  const hasWarnings = task.status === 'SUCCEEDED' && (warnings.length > 0 || qualityIssues.length > 0)

  if (task.status === 'FAILED' || task.status === 'CANCELED') {
    const failureMessage = task.error || latestFailure?.message || '学术翻译未完成'
    return {
      tone: 'failed',
      label: task.status === 'CANCELED' ? '任务已取消' : '任务失败',
      detail: `${failureMessage} ${buildFailureGuidance(failureMessage)}`.trim(),
      stage,
      elapsedLabel,
    }
  }

  if (!TERMINAL_TASK_STATUSES.has(task.status)) {
    return {
      tone: 'running',
      label: '正在执行',
      detail: latestHeartbeat?.message || task.current_step || '正在执行学术翻译，请保持页面开启。',
      stage,
      elapsedLabel,
    }
  }

  if (hasWarnings) {
    const warningParts = [
      qualityIssues.length ? `${qualityIssues.length} 个待复核项` : '',
      warnings.length ? `${warnings.length} 条运行提示` : '',
    ].filter(Boolean)
    return {
      tone: 'warning',
      label: '已完成，存在警告',
      detail: `任务已完成，但仍有${warningParts.join('、')}。请先查看“质量检查”，确认是否需要重新翻译或人工复核。`,
      stage,
      elapsedLabel,
    }
  }

  return {
    tone: 'success',
    label: '已完成',
    detail: '译文与相关文件已生成。建议先核对 PDF 预览，再下载产物。',
    stage,
    elapsedLabel,
  }
}

export function getTranslationEventTone(event: TranslationTaskEvent): 'stage' | 'heartbeat' | 'success' | 'failed' {
  if (event.type === 'translation.heartbeat') return 'heartbeat'
  if (event.type === 'task.completed') return 'success'
  if (event.type === 'task.failed') return 'failed'
  return 'stage'
}

export function getTranslationEventMeta(event: TranslationTaskEvent): string {
  if (event.type === 'translation.heartbeat') {
    return event.elapsed_seconds != null ? `心跳 · ${formatElapsedSeconds(event.elapsed_seconds)}` : '心跳'
  }
  if (event.type === 'task.completed') return '完成'
  if (event.type === 'task.failed') return '失败'
  return `${event.progress}%`
}

export function selectTranslationPreviewState(
  files: TranslationFile[],
  segments: TranslationSegment[],
  request?: TranslationRequest,
): TranslationPreviewState {
  const file = selectTranslationPreviewFile(files, request)
  if (file) return { mode: 'pdf', file }
  if (segments.length > 0) return { mode: 'segments', file: null }
  return { mode: 'empty', file: null }
}

export function selectTranslationPreviewFile(
  files: TranslationFile[],
  request?: TranslationRequest,
): TranslationFile | null {
  const preferredKinds = request?.bilingual ? ['pdf_bilingual', 'pdf_monolingual'] : ['pdf_monolingual', 'pdf_bilingual']
  for (const kind of preferredKinds) {
    const match = files.find((file) => file.kind === kind)
    if (match) return match
  }
  return files.find((file) => PDF_FILE_KINDS.includes(file.kind as (typeof PDF_FILE_KINDS)[number])) ?? null
}

export function sortTranslationFiles(files: TranslationFile[], previewFile: TranslationFile | null): TranslationFile[] {
  const previewKind = previewFile?.kind ?? null
  return [...files].sort((left, right) => {
    if (previewKind && left.kind === previewKind && right.kind !== previewKind) return -1
    if (previewKind && right.kind === previewKind && left.kind !== previewKind) return 1
    const leftIndex = FILE_KIND_ORDER.indexOf(left.kind as (typeof FILE_KIND_ORDER)[number])
    const rightIndex = FILE_KIND_ORDER.indexOf(right.kind as (typeof FILE_KIND_ORDER)[number])
    const leftRank = leftIndex === -1 ? FILE_KIND_ORDER.length : leftIndex
    const rightRank = rightIndex === -1 ? FILE_KIND_ORDER.length : rightIndex
    if (leftRank !== rightRank) return leftRank - rightRank
    return left.label.localeCompare(right.label, 'zh-CN')
  })
}

function getFileSuffix(fileName?: string | null): string {
  return fileName?.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
}

function stringifyGlossary(glossary?: Record<string, unknown>): string {
  if (!glossary || Array.isArray(glossary) || typeof glossary !== 'object') return '{}'
  const cleanEntries: Array<[string, string]> = []
  for (const [source, target] of Object.entries(glossary)) {
    if (!source.trim() || typeof target !== 'string' || !target.trim()) continue
    cleanEntries.push([source.trim(), target.trim()])
  }
  const clean = Object.fromEntries(cleanEntries)
  return JSON.stringify(clean, null, 2)
}

function normalizeLanguageCode(value: unknown, fallback: string): string {
  const code = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return code || fallback
}

function normalizeParallel(value: unknown): number {
  const parsed = Number(value)
  if (Number.isInteger(parsed) && parsed >= 1 && parsed <= 5) return parsed
  return DEFAULT_TRANSLATION_FORM_STATE.parallel
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter((value) => typeof value === 'string' && value.trim()).map((value) => value.trim()))]
}

function fallbackStage(status: string): string {
  if (status === 'SUCCEEDED') return '学术翻译完成'
  if (status === 'FAILED') return '学术翻译失败'
  if (status === 'CANCELED') return '任务已取消'
  return '正在准备任务'
}

function getTaskElapsedSeconds(task: ResearchTask, now: number): number | null {
  const startedAt = parseTimestamp(task.started_at ?? task.created_at)
  if (!startedAt) return null
  const finishedAt = parseTimestamp(task.finished_at)
  const end = finishedAt ?? now
  return Math.max(0, Math.round((end - startedAt) / 1000))
}

function parseTimestamp(value?: string | null): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

function formatElapsedSeconds(value: number | null | undefined): string {
  if (value == null || value < 1) return '0秒'
  if (value < 60) return `${Math.round(value)}秒`
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const seconds = Math.round(value % 60)
  if (hours > 0) return `${hours}时${minutes}分${seconds}秒`
  if (seconds === 0) return `${minutes}分`
  return `${minutes}分${seconds}秒`
}

function buildFailureGuidance(message: string): string {
  const normalized = message.toLowerCase()
  if (normalized.includes('timeout') || message.includes('超时')) {
    return '请缩小文档规模、关闭“保留 PDF 原版式”，或稍后重试。'
  }
  if (message.includes('上传的翻译文档不存在') || message.includes('文档不存在')) {
    return '请重新上传原始文档后再次提交。'
  }
  if (normalized.includes('json') || message.includes('术语表')) {
    return '请修正术语表 JSON 后重新提交。'
  }
  if (message.includes('保留 PDF 原版式') || message.includes('TRANSLATION_PDF2ZH_COMMAND')) {
    return '请关闭“保留 PDF 原版式”，或联系管理员检查 PDF 翻译环境。'
  }
  if (message.includes('未生成可用译文文件')) {
    return '请重新运行任务；若再次失败，需要检查后端日志与产物目录。'
  }
  return '请检查输入文档、翻译配置和后端运行环境后重试。'
}
