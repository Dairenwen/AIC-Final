<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft, CheckCircle2, Download, FileJson, FileText,
  Image, LoaderCircle, Presentation, Upload, X,
} from 'lucide-vue-next'

import { getData, http } from '@/api/http'

interface PptTask {
  id: string
  status: string
  progress: number
  current_step?: string
  error?: string
  output: {
    slide_count?: number
    evidence_count?: number
    visual_count?: number
    source_file?: string
  }
}

const router = useRouter()
const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const audience = ref('科研团队与课题组')
const slides = ref<number | null>(10)
const language = ref('中文')
const tone = ref('专业、清晰、适合学术汇报')
const focus = ref('研究背景,核心方法,实验结果,核心结论')
const requirements = ref('')
const task = ref<PptTask | null>(null)
const busy = ref(false)
const errorMessage = ref('')
let pollTimer: number | null = null

const canGenerate = computed(() => Boolean(selectedFile.value) && !busy.value)
const currentPhase = computed(() => {
  const progress = task.value?.progress || 0
  if (progress >= 68) return 3
  if (progress >= 55) return 2
  if (progress >= 5) return 1
  return 0
})

function chooseFile() {
  fileInput.value?.click()
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  errorMessage.value = ''
  if (file && file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    errorMessage.value = '请选择 PDF 文献文件'
    selectedFile.value = null
    return
  }
  selectedFile.value = file
  task.value = null
}

function clearFile() {
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
}

async function generatePpt() {
  if (!selectedFile.value || busy.value) return
  if (slides.value !== null && (slides.value < 3 || slides.value > 30)) {
    errorMessage.value = 'PPT 页数必须在 3 到 30 页之间'
    return
  }
  busy.value = true
  errorMessage.value = ''
  task.value = null
  const form = new FormData()
  form.append('file', selectedFile.value)
  form.append('audience', audience.value.trim())
  if (slides.value !== null) form.append('slides', String(slides.value))
  form.append('language', language.value.trim())
  form.append('tone', tone.value.trim())
  form.append('focus', focus.value.trim())
  form.append('requirements', requirements.value.trim())
  try {
    const response = await http.post('/tools/literature-ppt/generate', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    })
    task.value = response.data.data as PptTask
    startPolling()
  } catch (error) {
    busy.value = false
    errorMessage.value = requestError(error)
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(loadTask, 1200)
  void loadTask()
}

async function loadTask() {
  if (!task.value) return
  try {
    task.value = await getData<PptTask>(`/tasks/${task.value.id}`)
    if (['SUCCEEDED', 'FAILED', 'CANCELED'].includes(task.value.status)) {
      busy.value = false
      stopPolling()
      if (task.value.status === 'FAILED') errorMessage.value = task.value.error || '文献 PPT 生成失败'
    }
  } catch (error) {
    busy.value = false
    stopPolling()
    errorMessage.value = requestError(error)
  }
}

async function downloadArtifact(kind: 'literature-ppt' | 'literature-evidence') {
  if (!task.value) return
  try {
    const response = await http.get(`/tasks/${task.value.id}/artifacts/${kind}`, { responseType: 'blob', timeout: 60000 })
    const extension = kind === 'literature-ppt' ? 'pptx' : 'json'
    const stem = (selectedFile.value?.name || 'literature').replace(/\.pdf$/i, '')
    downloadBlob(response.data, `${stem}.${kind === 'literature-ppt' ? 'presentation' : 'evidence'}.${extension}`)
  } catch (error) {
    errorMessage.value = requestError(error)
  }
}

function downloadBlob(value: Blob, filename: string) {
  const url = URL.createObjectURL(value)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '文献 PPT 工具执行失败'
}

function stopPolling() {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  pollTimer = null
}

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="workspace-page literature-ppt-page">
    <header class="research-tool-header">
      <button class="icon-button" type="button" title="返回科研工具集" aria-label="返回科研工具集" @click="router.push('/tools')"><ArrowLeft :size="17" /></button>
      <span class="research-tool-mark"><Presentation :size="19" /></span>
      <div><h1>文献 PPT 绘制</h1><p>从 PDF 提取可追溯证据，生成可编辑科研汇报 PPT</p></div>
    </header>

    <main class="literature-ppt-workspace">
      <section class="literature-ppt-form-panel">
        <div class="research-tool-section-title"><FileText :size="16" /><strong>文献与汇报参数</strong></div>
        <input ref="fileInput" class="sr-only" type="file" accept=".pdf,application/pdf" @change="onFileChange" />
        <button v-if="!selectedFile" class="literature-ppt-dropzone" type="button" @click="chooseFile">
          <span><Upload :size="21" /></span><strong>上传 PDF 文献</strong><small>最大 50 MB，系统将解析正文、表格和插图</small>
        </button>
        <div v-else class="literature-ppt-file">
          <span><FileText :size="18" /></span><div><strong>{{ selectedFile.name }}</strong><small>{{ (selectedFile.size / 1024 / 1024).toFixed(2) }} MB</small></div><button class="icon-button" type="button" title="移除文件" aria-label="移除文件" :disabled="busy" @click="clearFile"><X :size="15" /></button>
        </div>

        <div class="research-tool-form two-columns literature-ppt-options">
          <label><span>目标受众</span><input v-model="audience" maxlength="120" placeholder="例如：课题组、评审专家" /></label>
          <label><span>目标页数</span><input v-model.number="slides" type="number" min="3" max="30" /></label>
          <label><span>输出语言</span><select v-model="language"><option>中文</option><option>English</option><option>中英双语</option></select></label>
          <label><span>表达语气</span><input v-model="tone" maxlength="120" /></label>
          <label class="full"><span>重点内容</span><input v-model="focus" maxlength="500" placeholder="多个重点用逗号分隔" /></label>
          <label class="full"><span>其他要求</span><textarea v-model="requirements" rows="4" maxlength="1000" placeholder="例如：弱化推导过程，突出实验对比与应用价值"></textarea></label>
        </div>
        <p v-if="errorMessage" class="research-tool-error">{{ errorMessage }}</p>
        <button class="research-tool-run" type="button" :disabled="!canGenerate" @click="generatePpt"><LoaderCircle v-if="busy" class="spin" :size="17" /><Presentation v-else :size="17" />{{ busy ? '正在生成 PPT' : '开始生成 PPT' }}</button>
      </section>

      <section class="literature-ppt-result-panel">
        <div class="research-tool-section-title"><Presentation :size="16" /><strong>生成过程与结果</strong></div>
        <div v-if="!task" class="literature-ppt-empty"><Presentation :size="34" /><strong>等待生成</strong><span>上传文献并设置参数后，系统将生成可编辑 PPTX。</span></div>
        <template v-else>
          <div class="literature-ppt-progress-head"><div><span>{{ task.status === 'SUCCEEDED' ? '生成完成' : '任务执行中' }}</span><strong>{{ task.current_step }}</strong></div><b>{{ task.progress }}%</b></div>
          <div class="literature-ppt-progress"><span :style="{ width: `${task.progress}%` }"></span></div>
          <div class="literature-ppt-phases">
            <article :class="{ active: currentPhase === 1, done: currentPhase > 1 || task.status === 'SUCCEEDED' }"><span><CheckCircle2 v-if="currentPhase > 1 || task.status === 'SUCCEEDED'" :size="16" /><LoaderCircle v-else-if="currentPhase === 1" class="spin" :size="16" /><FileText v-else :size="16" /></span><div><strong>解析文献</strong><small>提取正文、章节与来源位置</small></div></article>
            <article :class="{ active: currentPhase === 2, done: currentPhase > 2 || task.status === 'SUCCEEDED' }"><span><CheckCircle2 v-if="currentPhase > 2 || task.status === 'SUCCEEDED'" :size="16" /><LoaderCircle v-else-if="currentPhase === 2" class="spin" :size="16" /><Image v-else :size="16" /></span><div><strong>组织证据</strong><small>识别原文表格与论文插图</small></div></article>
            <article :class="{ active: currentPhase === 3 && task.status !== 'SUCCEEDED', done: task.status === 'SUCCEEDED' }"><span><CheckCircle2 v-if="task.status === 'SUCCEEDED'" :size="16" /><LoaderCircle v-else-if="currentPhase === 3" class="spin" :size="16" /><Presentation v-else :size="16" /></span><div><strong>生成 PPT</strong><small>规划内容并输出可编辑页面</small></div></article>
          </div>
          <div v-if="task.status === 'SUCCEEDED'" class="literature-ppt-result">
            <div class="literature-ppt-metrics"><div><span>幻灯片</span><strong>{{ task.output.slide_count || 0 }} 页</strong></div><div><span>证据块</span><strong>{{ task.output.evidence_count || 0 }}</strong></div><div><span>图表项</span><strong>{{ task.output.visual_count || 0 }}</strong></div></div>
            <div class="literature-ppt-downloads"><button class="primary-button" type="button" @click="downloadArtifact('literature-ppt')"><Download :size="15" />下载可编辑 PPTX</button><button class="secondary-button" type="button" @click="downloadArtifact('literature-evidence')"><FileJson :size="15" />下载证据 JSON</button></div>
            <p>每页内容均基于解析后的原文证据，表格保持可编辑，插图附带来源位置。</p>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>
