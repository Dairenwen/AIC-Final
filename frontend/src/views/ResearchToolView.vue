<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, BarChart3, BookOpenText, Check, Clipboard, Download, FileOutput, LoaderCircle, Table2, WandSparkles } from 'lucide-vue-next'

import { http } from '@/api/http'
import { renderMarkdown } from '@/utils/renderMarkdown'

type ToolMode = 'citation' | 'table' | 'statistics' | 'docx'

const route = useRoute()
const router = useRouter()
const busy = ref(false)
const errorMessage = ref('')
const copied = ref('')
const outputTab = ref('primary')

const citation = ref({ title: '', authors: '', year: new Date().getFullYear(), venue: '', entryType: 'article', doi: '', volume: '', pages: '' })
const citationResult = ref<Record<string, string> | null>(null)
const table = ref({ source: '模型\t准确率 (%)\tF1\nBaseline\t82.3\t80.7\nOurs\t91.6\t90.9', delimiter: 'auto', caption: '实验结果', label: 'main_results' })
const tableResult = ref<{ markdown: string; latex: string; preview: string[][]; rowCount: number; columnCount: number } | null>(null)
const statisticsText = ref('')
const statisticsResult = ref<Record<string, number | Array<{ term: string; count: number }>> | null>(null)
const documentForm = ref({ filename: '科研文稿', markdown: '# 科研文稿\n\n在此输入 Markdown 内容。\n\n| 指标 | 数值 |\n| --- | --- |\n| Accuracy | 91.6% |' })

const mode = computed<ToolMode>(() => {
  if (route.path.endsWith('citation-formatter')) return 'citation'
  if (route.path.endsWith('table-converter')) return 'table'
  if (route.path.endsWith('text-statistics')) return 'statistics'
  return 'docx'
})
const metadata = computed(() => ({
  citation: { title: '文献引用格式化', subtitle: 'BibTeX、APA 与 GB/T 7714', icon: BookOpenText },
  table: { title: '科研表格转换', subtitle: 'CSV / TSV 转 Markdown 与 LaTeX', icon: Table2 },
  statistics: { title: '学术文本统计', subtitle: '双语字数、句段与关键词分析', icon: BarChart3 },
  docx: { title: 'Markdown 转 Word', subtitle: '复用文稿 Agent 的 DOCX 转换能力', icon: FileOutput },
}[mode.value]))
const markdownPreview = computed(() => renderMarkdown(documentForm.value.markdown))

watch(mode, () => { errorMessage.value = ''; copied.value = ''; outputTab.value = 'primary' })

async function execute() {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    if (mode.value === 'citation') {
      const response = await http.post('/tools/citation-formatter/format', citation.value)
      citationResult.value = response.data.data
    } else if (mode.value === 'table') {
      const response = await http.post('/tools/table-converter/convert', table.value)
      tableResult.value = response.data.data
    } else if (mode.value === 'statistics') {
      const response = await http.post('/tools/text-statistics/analyze', { text: statisticsText.value })
      statisticsResult.value = response.data.data
    } else {
      const response = await http.post('/tools/markdown-to-docx/export', documentForm.value, { responseType: 'blob', timeout: 30000 })
      downloadBlob(response.data, `${documentForm.value.filename || '科研文稿'}.docx`)
    }
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    busy.value = false
  }
}

async function copyText(value: string, key: string) {
  await navigator.clipboard.writeText(value)
  copied.value = key
  window.setTimeout(() => { if (copied.value === key) copied.value = '' }, 1400)
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
  const value = error as { response?: { data?: { error?: { message?: string } | Blob } }; message?: string }
  return (value.response?.data as { error?: { message?: string } })?.error?.message || value.message || '工具执行失败'
}

const citationOutputs = computed(() => citationResult.value ? [
  { key: 'bibtex', label: 'BibTeX', value: citationResult.value.bibtex },
  { key: 'apa', label: 'APA', value: citationResult.value.apa },
  { key: 'gbt7714', label: 'GB/T 7714', value: citationResult.value.gbt7714 },
  { key: 'inline', label: '行内引用', value: citationResult.value.inline },
] : [])
const activeCitationOutput = computed(() => citationOutputs.value.find((item) => item.key === outputTab.value) ?? citationOutputs.value[0])
const activeTableOutput = computed(() => outputTab.value === 'latex' ? tableResult.value?.latex : tableResult.value?.markdown)
</script>

<template>
  <div class="workspace-page research-tool-page">
    <header class="research-tool-header">
      <button class="icon-button" type="button" title="返回科研工具集" @click="router.push('/tools')"><ArrowLeft :size="17" /></button>
      <span class="research-tool-mark"><component :is="metadata.icon" :size="19" /></span>
      <div><h1>{{ metadata.title }}</h1><p>{{ metadata.subtitle }}</p></div>
    </header>

    <main class="research-tool-workspace">
      <section class="research-tool-input">
        <div class="research-tool-section-title"><WandSparkles :size="16" /><strong>输入参数</strong></div>

        <div v-if="mode === 'citation'" class="research-tool-form two-columns">
          <label class="full"><span>论文标题</span><input v-model="citation.title" placeholder="输入完整论文标题" /></label>
          <label class="full"><span>作者</span><input v-model="citation.authors" placeholder="多位作者用分号分隔" /></label>
          <label><span>发表年份</span><input v-model.number="citation.year" type="number" min="1000" max="2200" /></label>
          <label><span>文献类型</span><select v-model="citation.entryType"><option value="article">期刊论文</option><option value="inproceedings">会议论文</option><option value="book">图书</option><option value="thesis">学位论文</option><option value="misc">其他</option></select></label>
          <label class="full"><span>期刊或会议</span><input v-model="citation.venue" placeholder="期刊名、会议名或出版机构" /></label>
          <label><span>卷号</span><input v-model="citation.volume" placeholder="可选" /></label>
          <label><span>页码</span><input v-model="citation.pages" placeholder="例如 12-24" /></label>
          <label class="full"><span>DOI</span><input v-model="citation.doi" placeholder="可选，例如 10.1000/example" /></label>
        </div>

        <div v-else-if="mode === 'table'" class="research-tool-form">
          <label><span>CSV / TSV 数据</span><textarea v-model="table.source" rows="15" spellcheck="false"></textarea></label>
          <div class="research-tool-inline-fields">
            <label><span>分隔符</span><select v-model="table.delimiter"><option value="auto">自动识别</option><option value="comma">逗号</option><option value="tab">制表符</option><option value="semicolon">分号</option></select></label>
            <label><span>表格标题</span><input v-model="table.caption" /></label>
            <label><span>LaTeX 标签</span><input v-model="table.label" /></label>
          </div>
        </div>

        <div v-else-if="mode === 'statistics'" class="research-tool-form">
          <label><span>学术文本</span><textarea v-model="statisticsText" rows="21" placeholder="粘贴摘要、章节或完整文稿" spellcheck="false"></textarea></label>
          <small class="research-tool-counter">{{ statisticsText.length.toLocaleString() }} / 200,000 字符</small>
        </div>

        <div v-else class="research-tool-form">
          <label><span>文件名</span><input v-model="documentForm.filename" maxlength="80" /></label>
          <label><span>Markdown 文稿</span><textarea v-model="documentForm.markdown" rows="19" spellcheck="false"></textarea></label>
        </div>

        <p v-if="errorMessage" class="research-tool-error">{{ errorMessage }}</p>
        <button class="research-tool-run" type="button" :disabled="busy" @click="execute">
          <LoaderCircle v-if="busy" class="spin" :size="17" />
          <Download v-else-if="mode === 'docx'" :size="17" />
          <WandSparkles v-else :size="17" />
          {{ mode === 'docx' ? '生成并下载 Word' : '开始处理' }}
        </button>
      </section>

      <section class="research-tool-output">
        <div class="research-tool-section-title"><component :is="metadata.icon" :size="16" /><strong>处理结果</strong></div>

        <div v-if="mode === 'citation' && citationResult" class="research-tool-result">
          <div class="research-tool-tabs"><button v-for="item in citationOutputs" :key="item.key" :class="{ active: outputTab === item.key || (outputTab === 'primary' && item.key === 'bibtex') }" @click="outputTab = item.key">{{ item.label }}</button></div>
          <div class="research-tool-code"><button title="复制结果" @click="copyText(activeCitationOutput?.value || '', activeCitationOutput?.key || '')"><Check v-if="copied === activeCitationOutput?.key" :size="15" /><Clipboard v-else :size="15" /></button><pre>{{ activeCitationOutput?.value }}</pre></div>
          <dl class="research-tool-summary"><div><dt>引用键</dt><dd>{{ citationResult.citationKey }}</dd></div></dl>
        </div>

        <div v-else-if="mode === 'table' && tableResult" class="research-tool-result">
          <div class="research-tool-tabs"><button :class="{ active: outputTab !== 'latex' }" @click="outputTab = 'primary'">Markdown</button><button :class="{ active: outputTab === 'latex' }" @click="outputTab = 'latex'">LaTeX</button></div>
          <div class="research-tool-code"><button title="复制结果" @click="copyText(activeTableOutput || '', outputTab)"><Check v-if="copied === outputTab" :size="15" /><Clipboard v-else :size="15" /></button><pre>{{ activeTableOutput }}</pre></div>
          <div class="research-tool-table-wrap"><table><tbody><tr v-for="(row, rowIndex) in tableResult.preview" :key="rowIndex"><component :is="rowIndex === 0 ? 'th' : 'td'" v-for="(cell, columnIndex) in row" :key="columnIndex">{{ cell }}</component></tr></tbody></table></div>
          <p class="research-tool-note">共 {{ tableResult.rowCount }} 行、{{ tableResult.columnCount }} 列</p>
        </div>

        <div v-else-if="mode === 'statistics' && statisticsResult" class="research-tool-result">
          <div class="research-stat-grid"><div><span>总字符</span><strong>{{ statisticsResult.characters }}</strong></div><div><span>中文字符</span><strong>{{ statisticsResult.chineseCharacters }}</strong></div><div><span>英文词数</span><strong>{{ statisticsResult.englishWords }}</strong></div><div><span>句子</span><strong>{{ statisticsResult.sentences }}</strong></div><div><span>段落</span><strong>{{ statisticsResult.paragraphs }}</strong></div><div><span>阅读时间</span><strong>{{ statisticsResult.estimatedReadingMinutes }} 分钟</strong></div></div>
          <div class="research-keyword-panel"><h2>高频关键词</h2><div><span v-for="item in statisticsResult.keywords as Array<{ term: string; count: number }>" :key="item.term">{{ item.term }}<small>{{ item.count }}</small></span></div></div>
        </div>

        <div v-else-if="mode === 'docx'" class="research-tool-document-preview markdown-document" v-html="markdownPreview"></div>

        <div v-else class="research-tool-empty"><component :is="metadata.icon" :size="30" /><strong>等待处理</strong><span>填写左侧参数后即可查看结果</span></div>
      </section>
    </main>
  </div>
</template>
