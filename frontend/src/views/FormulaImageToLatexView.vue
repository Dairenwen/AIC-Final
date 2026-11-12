<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Check,
  Clipboard,
  Copy,
  Download,
  FileImage,
  ImageUp,
  LoaderCircle,
  RefreshCw,
  Sigma,
  Trash2,
} from 'lucide-vue-next'

import { http, type ApiEnvelope } from '@/api/http'
import PageHeader from '@/components/PageHeader.vue'

interface RuntimeStatus {
  ready: boolean
  checks: Record<string, boolean>
  device: string
}

interface RecognitionResult {
  latex: string
  fileName: string
  device: string
  durationMs: number
}

const MAX_FILE_BYTES = 10 * 1024 * 1024
const ACCEPTED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/tiff'])
const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const previewUrl = ref('')
const latex = ref('')
const result = ref<RecognitionResult | null>(null)
const runtime = ref<RuntimeStatus | null>(null)
const runtimeLoading = ref(true)
const busy = ref(false)
const dragging = ref(false)
const copied = ref(false)
const errorMessage = ref('')

const fileMeta = computed(() => {
  if (!selectedFile.value) return ''
  return `${selectedFile.value.name} · ${formatBytes(selectedFile.value.size)}`
})

onMounted(() => {
  void loadRuntimeStatus()
  window.addEventListener('paste', handlePaste)
})

onBeforeUnmount(() => {
  window.removeEventListener('paste', handlePaste)
  revokePreview()
})

async function loadRuntimeStatus() {
  runtimeLoading.value = true
  try {
    const response = await http.get<ApiEnvelope<RuntimeStatus>>('/tools/formula-to-latex/status')
    runtime.value = response.data.data
  } catch {
    runtime.value = null
  } finally {
    runtimeLoading.value = false
  }
}

function openFilePicker() {
  fileInput.value?.click()
}

function handleFileInput(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) selectFile(file)
  input.value = ''
}

function handleDrop(event: DragEvent) {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) selectFile(file)
}

function handlePaste(event: ClipboardEvent) {
  const file = Array.from(event.clipboardData?.files ?? []).find((item) => item.type.startsWith('image/'))
  if (!file) return
  event.preventDefault()
  selectFile(file)
}

function selectFile(file: File) {
  errorMessage.value = ''
  if (!ACCEPTED_TYPES.has(file.type)) {
    errorMessage.value = '仅支持 PNG、JPG、WEBP、BMP 或 TIFF 图片。'
    return
  }
  if (file.size > MAX_FILE_BYTES) {
    errorMessage.value = '图片不能超过 10 MB。'
    return
  }
  revokePreview()
  selectedFile.value = file
  previewUrl.value = URL.createObjectURL(file)
  latex.value = ''
  result.value = null
  copied.value = false
}

function clearFile() {
  revokePreview()
  selectedFile.value = null
  latex.value = ''
  result.value = null
  copied.value = false
  errorMessage.value = ''
}

function revokePreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

async function recognizeFormula() {
  if (!selectedFile.value || busy.value) return
  busy.value = true
  errorMessage.value = ''
  copied.value = false
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    const response = await http.post<ApiEnvelope<RecognitionResult>>(
      '/tools/formula-to-latex/recognize',
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 360_000 },
    )
    result.value = response.data.data
    latex.value = response.data.data.latex
  } catch (error) {
    const apiError = error as { response?: { data?: { error?: { message?: string } } } }
    errorMessage.value = apiError.response?.data?.error?.message || '识别失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function copyLatex() {
  if (!latex.value) return
  await navigator.clipboard.writeText(latex.value)
  copied.value = true
  window.setTimeout(() => (copied.value = false), 1600)
}

function downloadLatex() {
  if (!latex.value) return
  const blob = new Blob([latex.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${selectedFile.value?.name.replace(/\.[^.]+$/, '') || 'formula'}.tex`
  anchor.click()
  URL.revokeObjectURL(url)
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="workspace-page formula-tool-page">
    <PageHeader eyebrow="RESEARCH TOOL" title="公式图片转 LaTeX" description="UniMERNet 本地公式识别工作台">
      <span class="formula-runtime" :class="{ ready: runtime?.ready }">
        <LoaderCircle v-if="runtimeLoading" class="formula-spin" :size="14" />
        <i v-else></i>
        {{ runtimeLoading ? '检查运行环境' : runtime?.ready ? `运行就绪 · ${runtime.device}` : '运行环境未就绪' }}
      </span>
    </PageHeader>

    <div class="formula-workspace">
      <section class="formula-input-pane">
        <div class="formula-pane-heading">
          <div><span>01</span><h2>公式图片</h2></div>
          <button v-if="selectedFile" class="formula-icon-button" type="button" title="清除图片" aria-label="清除图片" @click="clearFile"><Trash2 :size="16" /></button>
        </div>

        <input ref="fileInput" class="formula-file-input" type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff" @change="handleFileInput" />
        <button
          v-if="!selectedFile"
          class="formula-dropzone"
          :class="{ dragging }"
          type="button"
          @click="openFilePicker"
          @dragenter.prevent="dragging = true"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="handleDrop"
        >
          <ImageUp :size="30" />
          <strong>选择或拖入公式图片</strong>
          <span><Clipboard :size="13" />也可直接粘贴截图</span>
          <small>PNG、JPG、WEBP、BMP、TIFF · 最大 10 MB</small>
        </button>

        <div v-else class="formula-image-preview">
          <img :src="previewUrl" alt="待识别公式" />
          <footer><FileImage :size="15" /><span>{{ fileMeta }}</span><button type="button" @click="openFilePicker">更换</button></footer>
        </div>

        <div v-if="runtime && !runtime.ready" class="formula-runtime-warning">
          <Sigma :size="18" />
          <div><strong>UniMERNet 运行环境未就绪</strong><span>服务端尚缺少专用 Python 环境或模型权重。</span></div>
          <button type="button" title="重新检查" aria-label="重新检查运行环境" @click="loadRuntimeStatus"><RefreshCw :size="15" /></button>
        </div>
        <p v-if="errorMessage" class="formula-error">{{ errorMessage }}</p>

        <button class="formula-recognize-button" type="button" :disabled="!selectedFile || busy || !runtime?.ready" @click="recognizeFormula">
          <LoaderCircle v-if="busy" class="formula-spin" :size="17" />
          <Sigma v-else :size="17" />
          {{ busy ? '正在识别' : '识别公式' }}
        </button>
      </section>

      <section class="formula-output-pane">
        <div class="formula-pane-heading">
          <div><span>02</span><h2>LaTeX 源码</h2></div>
          <div class="formula-result-actions">
            <button type="button" :disabled="!latex" :title="copied ? '已复制' : '复制 LaTeX'" :aria-label="copied ? '已复制' : '复制 LaTeX'" @click="copyLatex"><Check v-if="copied" :size="16" /><Copy v-else :size="16" /></button>
            <button type="button" :disabled="!latex" title="下载 TEX" aria-label="下载 TEX" @click="downloadLatex"><Download :size="16" /></button>
          </div>
        </div>

        <div v-if="!latex" class="formula-empty-result">
          <Sigma :size="34" />
          <strong>{{ busy ? '正在解析公式结构' : '等待识别结果' }}</strong>
          <span v-if="busy">首次加载模型可能需要数十秒</span>
        </div>
        <div v-else class="formula-result-editor">
          <textarea v-model="latex" spellcheck="false" aria-label="LaTeX 识别结果"></textarea>
          <footer>
            <span>{{ latex.length }} 字符</span>
            <span v-if="result">{{ (result.durationMs / 1000).toFixed(1) }} 秒 · {{ result.device }}</span>
          </footer>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.formula-tool-page{max-width:1380px}.formula-runtime{min-height:30px;padding:0 10px;display:inline-flex;align-items:center;gap:7px;color:var(--warning);background:#fff9ed;border:1px solid #eeddbd;border-radius:5px;font-size:14px;font-weight:700}.formula-runtime.ready{color:var(--success);background:var(--green-100);border-color:#d6e8dc}.formula-runtime i{width:7px;height:7px;background:currentColor;border-radius:50%}.formula-workspace{min-height:620px;display:grid;grid-template-columns:minmax(360px,.85fr) minmax(0,1.15fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}.formula-input-pane,.formula-output-pane{min-width:0;padding:22px;display:flex;flex-direction:column}.formula-input-pane{border-right:1px solid var(--line);background:var(--surface-soft)}.formula-pane-heading{min-height:34px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between}.formula-pane-heading>div{display:flex;align-items:center;gap:9px}.formula-pane-heading span{color:var(--muted);font-size:13px;font-weight:800}.formula-pane-heading h2{margin:0;font-size:16px}.formula-icon-button,.formula-result-actions button{width:32px;height:32px;display:grid;place-items:center;color:var(--muted);background:#fff;border:1px solid var(--line);border-radius:5px}.formula-file-input{display:none}.formula-dropzone{min-height:350px;padding:28px;display:grid;place-items:center;align-content:center;gap:10px;color:var(--muted);background:#fff;border:1px dashed #b8c5bd;border-radius:7px;text-align:center}.formula-dropzone:hover,.formula-dropzone.dragging{color:var(--green-900);background:var(--green-100);border-color:var(--green-700)}.formula-dropzone strong{color:var(--ink);font-size:16px}.formula-dropzone span{display:flex;align-items:center;gap:5px}.formula-dropzone small{font-size:13px}.formula-image-preview{min-height:350px;display:grid;grid-template-rows:minmax(0,1fr) auto;border:1px solid var(--line);border-radius:7px;overflow:hidden;background:#fff}.formula-image-preview>img{width:100%;height:310px;padding:18px;display:block;object-fit:contain;background:#f8faf9}.formula-image-preview footer{min-width:0;min-height:46px;padding:8px 11px;display:flex;align-items:center;gap:7px;border-top:1px solid var(--line)}.formula-image-preview footer span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:14px}.formula-image-preview footer button{margin-left:auto;padding:5px 8px;color:var(--green-700);background:transparent;font-weight:700}.formula-runtime-warning{margin-top:14px;padding:11px 12px;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;color:#7a5a16;background:#fff9ed;border-left:3px solid var(--warning)}.formula-runtime-warning strong,.formula-runtime-warning span{display:block}.formula-runtime-warning span{margin-top:2px;font-size:13px}.formula-runtime-warning button{padding:5px;display:grid;place-items:center;color:inherit;background:transparent}.formula-error{margin:12px 0 0;color:var(--danger);font-size:14px}.formula-recognize-button{min-height:40px;margin-top:auto;padding:0 16px;display:flex;align-items:center;justify-content:center;gap:7px;color:#fff;background:var(--green-900);border-radius:6px;font-weight:800}.formula-recognize-button:disabled{cursor:not-allowed;opacity:.45}.formula-result-actions{display:flex;gap:6px}.formula-result-actions button:disabled{cursor:not-allowed;opacity:.4}.formula-empty-result{flex:1;min-height:460px;display:grid;place-items:center;align-content:center;gap:10px;color:#93a099;text-align:center}.formula-empty-result strong{color:var(--muted);font-size:15px}.formula-empty-result span{font-size:13px}.formula-result-editor{flex:1;min-height:500px;display:grid;grid-template-rows:minmax(0,1fr) auto;border:1px solid var(--line);border-radius:7px;overflow:hidden}.formula-result-editor textarea{width:100%;min-height:470px;padding:18px;resize:none;color:#e8efeb;background:#17211c;border:0;outline:0;font:14px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace}.formula-result-editor footer{min-height:38px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;color:var(--muted);background:#fff;font-size:13px}.formula-spin{animation:formula-spin .85s linear infinite}@keyframes formula-spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.formula-workspace{grid-template-columns:1fr}.formula-input-pane{border-right:0;border-bottom:1px solid var(--line)}.formula-dropzone,.formula-image-preview{min-height:300px}.formula-image-preview>img{height:260px}}
@media(max-width:600px){.formula-tool-page{padding-left:14px;padding-right:14px}.formula-input-pane,.formula-output-pane{padding:16px}.formula-workspace{min-height:0}.formula-dropzone{min-height:260px}.formula-result-editor,.formula-empty-result{min-height:380px}.formula-result-editor textarea{min-height:350px}}
</style>
