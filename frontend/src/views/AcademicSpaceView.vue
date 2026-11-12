<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  AlertTriangle,
  BookMarked,
  CheckSquare2,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  ExternalLink,
  FileText,
  Folder,
  FolderOpen,
  HardDriveUpload,
  Library,
  LoaderCircle,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-vue-next'

import { apiUrl, http } from '@/api/http'
import type { ApiEnvelope } from '@/api/http'
import type { PersonalKnowledgeFolder, PersonalKnowledgePaper, PlatformKnowledgePaper } from '@/types'

interface PageMeta { total: number; page: number; size: number; pages: number }
interface UploadDuplicate {
  status: 'DUPLICATE_FOUND'
  file_name: string
  match_reason: 'ARXIV_ID' | 'DOI' | 'TITLE' | 'FILE_SHA256'
  detected: Record<string, string | null>
  platform_paper?: PlatformKnowledgePaper
  existing_personal_paper?: PersonalKnowledgePaper
}
interface PendingUploadDuplicate extends UploadDuplicate { file: File }

const folders = ref<PersonalKnowledgeFolder[]>([])
const papers = ref<PersonalKnowledgePaper[]>([])
const platformPapers = ref<PlatformKnowledgePaper[]>([])
const selectedFolderId = ref<string | null>(null)
const loading = ref(true)
const paperLoading = ref(false)
const actionBusy = ref(false)
const errorMessage = ref('')
const search = ref('')
const page = ref(1)
const meta = ref<PageMeta>({ total: 0, page: 1, size: 20, pages: 1 })
const folderModalOpen = ref(false)
const uploadModalOpen = ref(false)
const platformModalOpen = ref(false)
const newFolder = ref({ name: '', parent_id: '', description: '', color: '#47745b' })
const uploadFiles = ref<File[]>([])
const uploadFolderId = ref('')
const pendingUploadDuplicate = ref<PendingUploadDuplicate | null>(null)
const uploadQueue = ref<File[]>([])
const platformFolderId = ref('')
const platformSearch = ref('')
const platformPage = ref(1)
const platformMeta = ref<PageMeta>({ total: 0, page: 1, size: 15, pages: 1 })
const selectedPlatformIds = ref<string[]>([])
const openPaperMenu = ref<string | null>(null)

const activeFolder = computed(() => folders.value.find((item) => item.id === selectedFolderId.value) ?? null)
const totalPapers = computed(() => folders.value.reduce((sum, item) => sum + item.paper_count, 0))
const folderRows = computed(() => {
  const children = new Map<string | null, PersonalKnowledgeFolder[]>()
  for (const folder of folders.value) children.set(folder.parent_id, [...(children.get(folder.parent_id) ?? []), folder])
  const result: Array<PersonalKnowledgeFolder & { depth: number }> = []
  const visit = (parentId: string | null, depth: number) => {
    for (const folder of children.get(parentId) ?? []) {
      result.push({ ...folder, depth })
      visit(folder.id, depth + 1)
    }
  }
  visit(null, 0)
  return result
})
const pageNumbers = computed(() => compactPages(meta.value.page, meta.value.pages))
const platformPageNumbers = computed(() => compactPages(platformMeta.value.page, platformMeta.value.pages))

async function loadFolders() {
  const response = await http.get<ApiEnvelope<PersonalKnowledgeFolder[]>>('/academic-space/folders')
  folders.value = response.data.data
  if (selectedFolderId.value && !folders.value.some((item) => item.id === selectedFolderId.value)) selectedFolderId.value = null
}

async function loadPapers(reset = false) {
  if (reset) page.value = 1
  paperLoading.value = true
  errorMessage.value = ''
  try {
    const response = await http.get<ApiEnvelope<PersonalKnowledgePaper[]>>('/academic-space/papers', {
      params: { folder_id: selectedFolderId.value || undefined, search: search.value || undefined, page: page.value, size: 20 },
    })
    papers.value = response.data.data
    meta.value = response.data.meta as unknown as PageMeta
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    paperLoading.value = false
  }
}

async function selectFolder(folderId: string | null) {
  selectedFolderId.value = folderId
  await loadPapers(true)
}

function openCreateFolder(parentId = '') {
  newFolder.value = { name: '', parent_id: parentId, description: '', color: '#47745b' }
  folderModalOpen.value = true
}

async function createFolder() {
  if (!newFolder.value.name.trim() || actionBusy.value) return
  actionBusy.value = true
  errorMessage.value = ''
  try {
    const response = await http.post<ApiEnvelope<PersonalKnowledgeFolder>>('/academic-space/folders', {
      ...newFolder.value,
      parent_id: newFolder.value.parent_id || null,
    })
    folderModalOpen.value = false
    await loadFolders()
    await selectFolder(response.data.data.id)
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionBusy.value = false
  }
}

async function deleteFolder(folder: PersonalKnowledgeFolder) {
  if (!window.confirm(`确定删除知识库“${folder.name}”吗？`)) return
  try {
    await http.delete(`/academic-space/folders/${folder.id}`)
    await loadFolders()
    await loadPapers(true)
  } catch (error) {
    errorMessage.value = requestError(error)
  }
}

function openUpload() {
  uploadFiles.value = []
  uploadFolderId.value = selectedFolderId.value || folders.value[0]?.id || ''
  uploadModalOpen.value = true
}

function chooseUploadFiles(event: Event) {
  uploadFiles.value = Array.from((event.target as HTMLInputElement).files ?? [])
}

async function uploadPapers() {
  if (!uploadFolderId.value || uploadFiles.value.length === 0 || actionBusy.value) return
  uploadQueue.value = [...uploadFiles.value]
  uploadModalOpen.value = false
  await processUploadQueue()
}

async function processUploadQueue() {
  actionBusy.value = true
  errorMessage.value = ''
  try {
    while (uploadQueue.value.length) {
      const file = uploadQueue.value.shift()!
      const form = new FormData()
      form.append('folder_id', uploadFolderId.value)
      form.append('file', file)
      const response = await http.post<ApiEnvelope<PersonalKnowledgePaper | UploadDuplicate>>(
        '/academic-space/papers/upload', form,
        { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60000 },
      )
      const result = response.data.data
      if ('status' in result && result.status === 'DUPLICATE_FOUND') {
        pendingUploadDuplicate.value = { ...result, file }
        return
      }
    }
    selectedFolderId.value = uploadFolderId.value
    await Promise.all([loadFolders(), loadPapers(true)])
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionBusy.value = false
  }
}

async function resolveUploadDuplicate(action: 'platform' | 'local' | 'skip') {
  const duplicate = pendingUploadDuplicate.value
  if (!duplicate || actionBusy.value) return
  actionBusy.value = true
  errorMessage.value = ''
  try {
    if (action === 'platform' && duplicate.platform_paper) {
      await http.post('/academic-space/platform-papers/import', {
        folder_id: uploadFolderId.value,
        paper_ids: [duplicate.platform_paper.id],
      })
    } else if (action === 'local') {
      const form = new FormData()
      form.append('folder_id', uploadFolderId.value)
      form.append('file', duplicate.file)
      form.append('force_local', 'true')
      await http.post('/academic-space/papers/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60000,
      })
    }
    pendingUploadDuplicate.value = null
  } catch (error) {
    errorMessage.value = requestError(error)
    return
  } finally {
    actionBusy.value = false
  }
  await processUploadQueue()
}

function duplicateReasonLabel(reason: UploadDuplicate['match_reason']) {
  return ({ ARXIV_ID: 'ArXiv ID 一致', DOI: 'DOI 一致', TITLE: '论文标题一致', FILE_SHA256: '文件内容完全一致' })[reason]
}

async function openPlatformPicker() {
  platformFolderId.value = selectedFolderId.value || folders.value[0]?.id || ''
  platformSearch.value = ''
  selectedPlatformIds.value = []
  platformPage.value = 1
  platformModalOpen.value = true
  await loadPlatformPapers()
}

async function loadPlatformPapers() {
  actionBusy.value = true
  try {
    const response = await http.get<ApiEnvelope<PlatformKnowledgePaper[]>>('/academic-space/platform-papers', {
      params: { search: platformSearch.value || undefined, page: platformPage.value, size: 15 },
    })
    platformPapers.value = response.data.data
    platformMeta.value = response.data.meta as unknown as PageMeta
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionBusy.value = false
  }
}

function togglePlatformPaper(id: string) {
  selectedPlatformIds.value = selectedPlatformIds.value.includes(id)
    ? selectedPlatformIds.value.filter((item) => item !== id)
    : [...selectedPlatformIds.value, id]
}

async function importPlatformPapers() {
  if (!platformFolderId.value || selectedPlatformIds.value.length === 0 || actionBusy.value) return
  actionBusy.value = true
  try {
    await http.post('/academic-space/platform-papers/import', {
      folder_id: platformFolderId.value,
      paper_ids: selectedPlatformIds.value,
    })
    platformModalOpen.value = false
    selectedFolderId.value = platformFolderId.value
    await Promise.all([loadFolders(), loadPapers(true)])
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionBusy.value = false
  }
}

async function movePaper(paper: PersonalKnowledgePaper, folderId: string) {
  if (!folderId || folderId === paper.folder_id) return
  try {
    await http.patch(`/academic-space/papers/${paper.id}`, { folder_id: folderId })
    openPaperMenu.value = null
    await Promise.all([loadFolders(), loadPapers()])
  } catch (error) {
    errorMessage.value = requestError(error)
  }
}

async function deletePaper(paper: PersonalKnowledgePaper) {
  if (!window.confirm(`确定从个人知识库移除“${paper.title}”吗？`)) return
  try {
    await http.delete(`/academic-space/papers/${paper.id}`)
    await Promise.all([loadFolders(), loadPapers()])
  } catch (error) {
    errorMessage.value = requestError(error)
  }
}

function downloadPaper(paper: PersonalKnowledgePaper) {
  window.open(apiUrl(`/academic-space/papers/${paper.id}/file`), '_blank', 'noopener')
}

async function changePage(value: number, platform = false) {
  if (platform) { platformPage.value = value; await loadPlatformPapers() }
  else { page.value = value; await loadPapers() }
}

function compactPages(current: number, total: number): Array<number | string> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const values = new Set([1, 2, total - 1, total, current - 1, current, current + 1].filter((item) => item >= 1 && item <= total))
  const result: Array<number | string> = []
  let previous = 0
  for (const value of [...values].sort((a, b) => a - b)) {
    if (previous && value - previous > 1) result.push(`ellipsis-${previous}`)
    result.push(value)
    previous = value
  }
  return result
}

function formatBytes(value: number | null) {
  if (!value) return ''
  return value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : ''
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '操作失败'
}

onMounted(async () => {
  try { await loadFolders(); await loadPapers() }
  catch (error) { errorMessage.value = requestError(error) }
  finally { loading.value = false }
})
</script>

<template>
  <div class="academic-space-page">
    <header class="academic-space-header">
      <div><span class="academic-space-mark"><Library :size="19" /></span><span><strong>学术空间</strong><small>个人科研资料与知识资产</small></span></div>
      <div class="academic-space-actions">
        <button class="secondary-button" type="button" :disabled="folders.length === 0" @click="openPlatformPicker"><Database :size="15" />从平台知识库加载</button>
        <button class="primary-button" type="button" :disabled="folders.length === 0" @click="openUpload"><HardDriveUpload :size="15" />上传 PDF</button>
      </div>
    </header>

    <main class="academic-space-workspace">
      <aside class="personal-kb-sidebar">
        <div class="personal-kb-title"><span><BookMarked :size="16" />我的知识库</span><button type="button" title="新建知识库" @click="openCreateFolder()"><Plus :size="17" /></button></div>
        <button class="personal-kb-all" :class="{ active: selectedFolderId === null }" @click="selectFolder(null)"><FolderOpen :size="16" /><span>全部文献</span><small>{{ totalPapers }}</small></button>
        <div class="personal-kb-folders">
          <div v-for="folder in folderRows" :key="folder.id" class="personal-kb-folder-row" :class="{ active: selectedFolderId === folder.id }" :style="{ '--folder-depth': folder.depth }">
            <button class="personal-kb-folder-main" type="button" @click="selectFolder(folder.id)"><Folder :size="16" :style="{ color: folder.color }" /><span>{{ folder.name }}</span><small>{{ folder.paper_count }}</small></button>
            <button class="personal-kb-folder-menu" type="button" title="添加子文件夹" @click="openCreateFolder(folder.id)"><Plus :size="14" /></button>
            <button class="personal-kb-folder-menu" type="button" title="删除知识库" @click="deleteFolder(folder)"><Trash2 :size="13" /></button>
          </div>
        </div>
        <button class="personal-kb-create" type="button" @click="openCreateFolder()"><Plus :size="15" />新建知识库</button>
      </aside>

      <section class="personal-kb-content">
        <div class="personal-kb-toolbar">
          <div><h1>{{ activeFolder?.name || '全部文献' }}</h1><p>{{ activeFolder?.description || `共收录 ${meta.total} 篇个人文献` }}</p></div>
          <label class="personal-kb-search"><Search :size="15" /><input v-model="search" placeholder="搜索标题或期刊" @keyup.enter="loadPapers(true)" /></label>
        </div>
        <p v-if="errorMessage" class="personal-kb-error">{{ errorMessage }}</p>
        <div v-if="loading || paperLoading" class="personal-kb-empty"><LoaderCircle class="spin" :size="28" /><strong>正在加载文献</strong></div>
        <div v-else-if="papers.length === 0" class="personal-kb-empty"><BookMarked :size="34" /><strong>知识库中暂无文献</strong><span>{{ folders.length ? '可上传 PDF 或从平台知识库加载' : '先创建一个知识库文件夹' }}</span><button v-if="folders.length === 0" class="primary-button" @click="openCreateFolder()"><Plus :size="15" />创建知识库</button></div>
        <div v-else class="personal-paper-list">
          <article v-for="paper in papers" :key="paper.id" class="personal-paper-card">
            <span class="personal-paper-icon"><FileText :size="19" /></span>
            <div class="personal-paper-info">
              <div class="personal-paper-heading"><h2>{{ paper.title }}</h2><span :class="['source-tag', paper.source_type.toLowerCase()]">{{ paper.source_type === 'LOCAL_UPLOAD' ? '本地 PDF' : '平台知识库' }}</span></div>
              <p v-if="paper.authors.length" class="personal-paper-authors">{{ paper.authors.join('、') }}</p>
              <p class="personal-paper-meta"><span v-if="paper.publish_venue">{{ paper.publish_venue }}</span><span v-if="paper.publish_year">{{ paper.publish_year }}</span><span v-if="paper.file_size">{{ formatBytes(paper.file_size) }}</span><span>加入于 {{ formatDate(paper.created_at) }}</span></p>
              <p v-if="paper.abstract" class="personal-paper-abstract">{{ paper.abstract }}</p>
            </div>
            <div class="personal-paper-actions">
              <button v-if="paper.source_type === 'LOCAL_UPLOAD'" type="button" title="下载 PDF" @click="downloadPaper(paper)"><Download :size="15" /></button>
              <a v-else-if="paper.source_url" :href="paper.source_url" target="_blank" rel="noopener" title="打开来源"><ExternalLink :size="15" /></a>
              <button type="button" title="更多操作" @click="openPaperMenu = openPaperMenu === paper.id ? null : paper.id"><MoreHorizontal :size="16" /></button>
              <div v-if="openPaperMenu === paper.id" class="personal-paper-menu">
                <label>移动到<select :value="paper.folder_id" @change="movePaper(paper, ($event.target as HTMLSelectElement).value)"><option v-for="folder in folderRows" :key="folder.id" :value="folder.id">{{ '　'.repeat(folder.depth) }}{{ folder.name }}</option></select></label>
                <button type="button" @click="deletePaper(paper)"><Trash2 :size="14" />移除文献</button>
              </div>
            </div>
          </article>
        </div>
        <nav v-if="meta.pages > 1" class="personal-kb-pagination" aria-label="文献分页"><button :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="15" /></button><template v-for="item in pageNumbers" :key="item"><span v-if="typeof item === 'string'">...</span><button v-else :class="{ active: item === page }" @click="changePage(item)">{{ item }}</button></template><button :disabled="page >= meta.pages" @click="changePage(page + 1)"><ChevronRight :size="15" /></button><small>共 {{ meta.pages }} 页</small></nav>
      </section>
    </main>

    <div v-if="folderModalOpen" class="personal-kb-modal-backdrop" @click.self="folderModalOpen = false"><section class="personal-kb-modal compact"><header><h2>新建知识库</h2><button @click="folderModalOpen = false"><X :size="17" /></button></header><div class="personal-kb-form"><label><span>名称</span><input v-model="newFolder.name" maxlength="120" autofocus placeholder="例如：动态 RAG" /></label><label><span>上级知识库</span><select v-model="newFolder.parent_id"><option value="">无，创建一级知识库</option><option v-for="folder in folderRows" :key="folder.id" :value="folder.id">{{ '　'.repeat(folder.depth) }}{{ folder.name }}</option></select></label><label><span>说明</span><textarea v-model="newFolder.description" rows="3" placeholder="可选"></textarea></label></div><footer><button class="secondary-button" @click="folderModalOpen = false">取消</button><button class="primary-button" :disabled="!newFolder.name.trim() || actionBusy" @click="createFolder">创建</button></footer></section></div>

    <div v-if="uploadModalOpen" class="personal-kb-modal-backdrop" @click.self="uploadModalOpen = false"><section class="personal-kb-modal compact"><header><h2>上传本地文献</h2><button @click="uploadModalOpen = false"><X :size="17" /></button></header><div class="personal-kb-form"><label><span>保存到</span><select v-model="uploadFolderId"><option v-for="folder in folderRows" :key="folder.id" :value="folder.id">{{ '　'.repeat(folder.depth) }}{{ folder.name }}</option></select></label><label class="personal-upload-drop"><HardDriveUpload :size="26" /><strong>选择 PDF 文件</strong><span>支持多选，单个文件不超过 50MB</span><input type="file" accept="application/pdf,.pdf" multiple @change="chooseUploadFiles" /></label><ul v-if="uploadFiles.length"><li v-for="file in uploadFiles" :key="file.name">{{ file.name }} <small>{{ formatBytes(file.size) }}</small></li></ul></div><footer><button class="secondary-button" @click="uploadModalOpen = false">取消</button><button class="primary-button" :disabled="!uploadFolderId || uploadFiles.length === 0 || actionBusy" @click="uploadPapers"><LoaderCircle v-if="actionBusy" class="spin" :size="15" />上传 {{ uploadFiles.length ? `(${uploadFiles.length})` : '' }}</button></footer></section></div>

    <div v-if="platformModalOpen" class="personal-kb-modal-backdrop" @click.self="platformModalOpen = false"><section class="personal-kb-modal platform-picker"><header><div><h2>从平台知识库加载</h2><p>引用平台文献及其已有切片，不重复复制公共数据</p></div><button @click="platformModalOpen = false"><X :size="17" /></button></header><div class="platform-picker-controls"><label><span>保存到</span><select v-model="platformFolderId"><option v-for="folder in folderRows" :key="folder.id" :value="folder.id">{{ '　'.repeat(folder.depth) }}{{ folder.name }}</option></select></label><label class="personal-kb-search"><Search :size="15" /><input v-model="platformSearch" placeholder="搜索标题、作者或摘要" @keyup.enter="platformPage = 1; loadPlatformPapers()" /></label><button class="secondary-button" @click="platformPage = 1; loadPlatformPapers()">搜索</button></div><div class="platform-picker-list"><button v-for="paper in platformPapers" :key="paper.id" type="button" :class="{ selected: selectedPlatformIds.includes(paper.id) }" @click="togglePlatformPaper(paper.id)"><span class="platform-paper-check"><CheckSquare2 v-if="selectedPlatformIds.includes(paper.id)" :size="17" /></span><span><strong>{{ paper.title }}</strong><small>{{ paper.authors.slice(0, 4).join('、') || '作者未知' }}<template v-if="paper.publish_year"> · {{ paper.publish_year }}</template><template v-if="paper.publish_venue"> · {{ paper.publish_venue }}</template></small></span></button><div v-if="!actionBusy && platformPapers.length === 0" class="personal-kb-empty"><Database :size="30" /><strong>未找到平台文献</strong></div></div><nav v-if="platformMeta.pages > 1" class="personal-kb-pagination"><button :disabled="platformPage <= 1" @click="changePage(platformPage - 1, true)"><ChevronLeft :size="15" /></button><template v-for="item in platformPageNumbers" :key="item"><span v-if="typeof item === 'string'">...</span><button v-else :class="{ active: item === platformPage }" @click="changePage(item, true)">{{ item }}</button></template><button :disabled="platformPage >= platformMeta.pages" @click="changePage(platformPage + 1, true)"><ChevronRight :size="15" /></button></nav><footer><span>已选择 {{ selectedPlatformIds.length }} 篇</span><div><button class="secondary-button" @click="platformModalOpen = false">取消</button><button class="primary-button" :disabled="!platformFolderId || selectedPlatformIds.length === 0 || actionBusy" @click="importPlatformPapers">确认加载</button></div></footer></section></div>
    <div v-if="pendingUploadDuplicate" class="personal-kb-modal-backdrop">
      <section class="personal-kb-modal compact duplicate-check-modal">
        <header>
          <div><h2><AlertTriangle :size="19" />检测到重复文献</h2><p>{{ duplicateReasonLabel(pendingUploadDuplicate.match_reason) }}</p></div>
          <button :disabled="actionBusy" @click="resolveUploadDuplicate('skip')"><X :size="17" /></button>
        </header>
        <div class="duplicate-check-body">
          <p class="duplicate-upload-file">正在上传：<strong>{{ pendingUploadDuplicate.file_name }}</strong></p>
          <article v-if="pendingUploadDuplicate.platform_paper" class="duplicate-platform-paper">
            <span><Database :size="19" /></span>
            <div>
              <small>平台知识库已有文献</small>
              <strong>{{ pendingUploadDuplicate.platform_paper.title }}</strong>
              <p>{{ pendingUploadDuplicate.platform_paper.authors.slice(0, 5).join('、') || '作者未知' }}</p>
              <p><span v-if="pendingUploadDuplicate.detected.arxiv_id">ArXiv {{ pendingUploadDuplicate.detected.arxiv_id }}</span><span v-if="pendingUploadDuplicate.detected.doi">DOI {{ pendingUploadDuplicate.detected.doi }}</span></p>
            </div>
          </article>
          <article v-else-if="pendingUploadDuplicate.existing_personal_paper" class="duplicate-platform-paper local">
            <span><FileText :size="19" /></span>
            <div><small>个人知识库已有相同文件</small><strong>{{ pendingUploadDuplicate.existing_personal_paper.title }}</strong></div>
          </article>
          <p class="duplicate-check-note">使用平台文献可直接复用已有元数据、切片和检索数据，不会重复保存 PDF。</p>
        </div>
        <footer>
          <button class="secondary-button" :disabled="actionBusy" @click="resolveUploadDuplicate('skip')">跳过此文件</button>
          <div>
            <button class="secondary-button" :disabled="actionBusy" @click="resolveUploadDuplicate('local')">仍保留本地 PDF</button>
            <button v-if="pendingUploadDuplicate.platform_paper" class="primary-button" :disabled="actionBusy" @click="resolveUploadDuplicate('platform')"><LoaderCircle v-if="actionBusy" class="spin" :size="15" />使用平台文献</button>
          </div>
        </footer>
      </section>
    </div>
  </div>
</template>
