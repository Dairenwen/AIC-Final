<script setup lang="ts">
import ZyIcon from '@/components/icons/ZyIcon.vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FileClock,
  FilePlus2,
  FileText,
  FolderArchive,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Save,
  Target,
} from 'lucide-vue-next'

import { getData, http } from '@/api/http'
import { agentPresentation } from '@/catalog/presentation'
import type { GlyphName } from '@/components/icons/glyphs'
import TaskComposer from '@/components/TaskComposer.vue'
import type { CatalogItem, ProjectConversation, ProjectDocument, ProjectTask, ProjectWorkspace } from '@/types'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.params.id))
const workspace = ref<ProjectWorkspace | null>(null)
const agents = ref<CatalogItem[]>([])
const activeDocument = ref<ProjectDocument | null>(null)
const activeConversationId = ref('')
const loading = ref(true)
const documentLoading = ref(false)
const saving = ref(false)
const saveState = ref('')
const workspaceError = ref('')
const activePane = ref<'context' | 'document' | 'assistant'>('document')

const canEdit = computed(() => workspace.value?.project.role !== 'VIEWER')
const agentRoutes = computed(() => Object.fromEntries(agents.value.filter((item) => item.code && item.route).map((item) => [item.code as string, item.route as string])))

async function loadWorkspace() {
  loading.value = true
  workspaceError.value = ''
  try {
    const [workspaceData, agentData] = await Promise.all([
      getData<ProjectWorkspace>(`/projects/${projectId.value}/workspace`),
      getData<CatalogItem[]>('/agents'),
    ])
    workspace.value = workspaceData
    agents.value = agentData
    if (!activeConversationId.value) {
      activeConversationId.value = workspaceData.conversations[0]?.id || ''
      if (!activeConversationId.value && workspaceData.project.role !== 'VIEWER') await createConversation()
    }
    if (!activeDocument.value && workspaceData.documents[0]) await openDocument(workspaceData.documents[0])
  } catch (error) {
    const value = error as { response?: { data?: { error?: { message?: string } } } }
    workspaceError.value = value.response?.data?.error?.message || '项目工作区加载失败'
  } finally {
    loading.value = false
  }
}

async function openDocument(document: ProjectDocument) {
  documentLoading.value = true
  saveState.value = ''
  activePane.value = 'document'
  try {
    activeDocument.value = await getData<ProjectDocument>(`/projects/${projectId.value}/documents/${document.id}`)
  } finally {
    documentLoading.value = false
  }
}

async function createDocument() {
  const response = await http.post(`/projects/${projectId.value}/documents`, {
    title: '未命名研究笔记',
    content: '# 研究笔记\n\n## 问题\n\n## 证据\n\n## 下一步\n',
    document_type: 'MARKDOWN',
  })
  activeDocument.value = response.data.data as ProjectDocument
  activePane.value = 'document'
  await refreshWorkspaceLists()
}

async function saveDocument() {
  if (!activeDocument.value || saving.value) return
  saving.value = true
  saveState.value = '正在保存'
  try {
    const response = await http.patch(`/projects/${projectId.value}/documents/${activeDocument.value.id}`, {
      title: activeDocument.value.title,
      content: activeDocument.value.content,
      version: activeDocument.value.version,
    })
    activeDocument.value = response.data.data as ProjectDocument
    saveState.value = `已保存 v${activeDocument.value.version}`
    await refreshWorkspaceLists()
  } catch (error) {
    const value = error as { response?: { status?: number; data?: { error?: { message?: string } } } }
    saveState.value = value.response?.status === 409 ? '版本冲突，请刷新文档' : value.response?.data?.error?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function createConversation() {
  const response = await http.post(`/projects/${projectId.value}/conversations`, { title: `研究对话 ${new Date().toLocaleDateString('zh-CN')}` })
  const conversation = response.data.data as ProjectConversation
  activeConversationId.value = conversation.id
  await refreshWorkspaceLists()
}

async function refreshWorkspaceLists() {
  workspace.value = await getData<ProjectWorkspace>(`/projects/${projectId.value}/workspace`)
}

function openTask(task: ProjectTask) {
  const path = agentRoutes.value[task.agent_code]
  if (path) void router.push({ path, query: { task: task.id, project: projectId.value } })
}

function formatDate(value?: string) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

/*
 * Resizable three-column layout.  The side columns keep a pixel width that
 * the person can drag; the document column takes what is left.  Widths are
 * remembered per browser.  Below SPLIT_MIN_WIDTH the stylesheet's stacked
 * and tabbed layouts take over and the handles go away.
 */
const SPLIT_KEY = 'zhiyan.workspace.split'
const SPLIT_DEFAULT = { left: 264, right: 420 }
// Narrower than this the page keeps its stacked layout (and below 850px the
// panes become tabs), because three readable columns no longer fit.
const SPLIT_MIN_WIDTH = 980
const SPLIT_LIMIT = { leftMin: 200, leftMax: 460, rightMin: 320, rightMax: 760, centerMin: 360, handles: 18 }
const rootEl = ref<HTMLElement | null>(null)
const splitEnabled = ref(false)
const rootWidth = ref(0)
const split = ref(readSplit())
const dragging = ref<'left' | 'right' | null>(null)
let rootObserver: ResizeObserver | null = null
let dragStart = { x: 0, left: 0, right: 0 }

function readSplit() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SPLIT_KEY) || 'null') as { left?: number; right?: number } | null
    if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.right)) return { left: Number(saved.left), right: Number(saved.right) }
  } catch {
    // Unreadable storage falls back to the default widths.
  }
  return { ...SPLIT_DEFAULT }
}

function saveSplit() {
  try {
    window.localStorage.setItem(SPLIT_KEY, JSON.stringify(split.value))
  } catch {
    // The widths then last for this visit only.
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

// Widths actually applied: the saved ones, narrowed when the window is too
// small to leave the document column its minimum.
const appliedSplit = computed(() => {
  const available = rootWidth.value - SPLIT_LIMIT.handles - SPLIT_LIMIT.centerMin
  let left = clamp(split.value.left, SPLIT_LIMIT.leftMin, SPLIT_LIMIT.leftMax)
  let right = clamp(split.value.right, SPLIT_LIMIT.rightMin, SPLIT_LIMIT.rightMax)
  if (available > 0 && left + right > available) {
    right = clamp(available - left, SPLIT_LIMIT.rightMin, right)
    left = clamp(available - right, SPLIT_LIMIT.leftMin, left)
  }
  return { left, right }
})
const splitStyle = computed(() => splitEnabled.value
  ? { '--ws-left': `${appliedSplit.value.left}px`, '--ws-right': `${appliedSplit.value.right}px` }
  : undefined)

function startDrag(side: 'left' | 'right', event: PointerEvent) {
  if (!splitEnabled.value) return
  dragging.value = side
  dragStart = { x: event.clientX, left: appliedSplit.value.left, right: appliedSplit.value.right }
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  event.preventDefault()
}

function onDrag(event: PointerEvent) {
  if (!dragging.value) return
  const delta = event.clientX - dragStart.x
  const room = rootWidth.value - SPLIT_LIMIT.handles - SPLIT_LIMIT.centerMin
  if (dragging.value === 'left') {
    const max = Math.min(SPLIT_LIMIT.leftMax, room - dragStart.right)
    split.value = { left: clamp(dragStart.left + delta, SPLIT_LIMIT.leftMin, max), right: dragStart.right }
  } else {
    const max = Math.min(SPLIT_LIMIT.rightMax, room - dragStart.left)
    split.value = { left: dragStart.left, right: clamp(dragStart.right - delta, SPLIT_LIMIT.rightMin, max) }
  }
}

function endDrag() {
  if (!dragging.value) return
  dragging.value = null
  saveSplit()
}

function resetSplit(side: 'left' | 'right') {
  split.value = { ...split.value, [side]: SPLIT_DEFAULT[side] }
  saveSplit()
}

function nudgeSplit(side: 'left' | 'right', step: number) {
  const current = appliedSplit.value
  split.value = side === 'left'
    ? { left: clamp(current.left + step, SPLIT_LIMIT.leftMin, SPLIT_LIMIT.leftMax), right: current.right }
    : { left: current.left, right: clamp(current.right - step, SPLIT_LIMIT.rightMin, SPLIT_LIMIT.rightMax) }
  saveSplit()
}

function agentIcon(agent: CatalogItem): GlyphName {
  return agentPresentation[agent.code ?? '']?.icon ?? 'agents'
}

onMounted(() => {
  void loadWorkspace()
  if (rootEl.value && typeof ResizeObserver !== 'undefined') {
    rootObserver = new ResizeObserver((entries) => {
      rootWidth.value = entries[0].contentRect.width
      splitEnabled.value = rootWidth.value > SPLIT_MIN_WIDTH
    })
    rootObserver.observe(rootEl.value)
  }
})

onBeforeUnmount(() => rootObserver?.disconnect())
</script>

<template>
  <div ref="rootEl" class="project-workspace-view">
    <div v-if="loading" class="workspace-loading"><LoaderCircle class="spin" :size="22" />正在进入项目工作区...</div>
    <div v-else-if="workspaceError" class="workspace-loading"><strong>{{ workspaceError }}</strong><RouterLink to="/projects">返回项目列表</RouterLink></div>
    <template v-else-if="workspace">
      <header class="workspace-header">
        <div class="workspace-header__identity">
          <RouterLink class="icon-button" to="/projects" aria-label="返回项目列表" title="返回项目列表"><ArrowLeft :size="17" /></RouterLink>
          <div><h1>{{ workspace.project.name }}</h1></div>
        </div>
        <div class="workspace-header__meta"><span>{{ workspace.project.role === 'OWNER' ? '项目负责人' : workspace.project.role === 'EDITOR' ? '协作编辑' : '只读成员' }}</span><span><Check :size="14" />工作区已同步</span></div>
      </header>

      <nav class="workspace-pane-switcher" aria-label="工作区视图" role="tablist">
        <button type="button" role="tab" :aria-selected="activePane === 'context'" :class="{ active: activePane === 'context' }" @click="activePane = 'context'">
          <Target :size="15" /><span>项目资料</span>
        </button>
        <button type="button" role="tab" :aria-selected="activePane === 'document'" :class="{ active: activePane === 'document' }" @click="activePane = 'document'">
          <FileText :size="15" /><span>文档</span>
        </button>
        <button type="button" role="tab" :aria-selected="activePane === 'assistant'" :class="{ active: activePane === 'assistant' }" @click="activePane = 'assistant'">
          <ZyIcon name="message" :size="16" /><span>项目助手</span>
        </button>
      </nav>

      <div class="workspace-grid" :class="{ 'workspace-grid--split': splitEnabled, 'workspace-grid--dragging': dragging }" :style="splitStyle">
        <aside class="workspace-context workspace-pane" :class="{ 'is-active': activePane === 'context' }">
          <section class="workspace-project-summary">
            <div class="workspace-section-label"><Target :size="14" />研究目标</div>
            <p>{{ workspace.project.research_goal || '尚未填写研究目标。' }}</p>
            <small>{{ workspace.project.description || '暂无项目说明' }}</small>
          </section>

          <section class="workspace-list-section">
            <header><span><FileText :size="14" />研究文档</span><button v-if="canEdit" class="icon-button" type="button" aria-label="新建文档" title="新建文档" @click="createDocument"><FilePlus2 :size="15" /></button></header>
            <button v-for="item in workspace.documents" :key="item.id" class="workspace-list-item" :class="{ active: activeDocument?.id === item.id }" type="button" @click="openDocument(item)">
              <span><strong>{{ item.title }}</strong><small>v{{ item.version }} · {{ formatDate(item.updated_at) }}</small></span><ChevronRight :size="14" />
            </button>
            <p v-if="!workspace.documents.length" class="workspace-list-empty">暂无文档</p>
          </section>

          <section class="workspace-list-section">
            <header><span><FileClock :size="14" />最近任务</span></header>
            <button v-for="task in workspace.tasks.slice(0, 6)" :key="task.id" class="workspace-list-item" type="button" :disabled="!agentRoutes[task.agent_code]" @click="openTask(task)">
              <span><strong>{{ task.title }}</strong><small>{{ task.status }} · {{ task.progress }}%</small></span><ChevronRight :size="14" />
            </button>
            <p v-if="!workspace.tasks.length" class="workspace-list-empty">项目内尚无 Agent 任务</p>
          </section>

          <section class="workspace-list-section">
            <header><span><FolderArchive :size="14" />研究产物</span><small>{{ workspace.artifacts.length }}</small></header>
            <div v-for="artifact in workspace.artifacts.slice(0, 5)" :key="artifact.id" class="workspace-artifact"><strong>{{ artifact.name }}</strong><small>{{ artifact.artifact_type }}</small></div>
            <p v-if="!workspace.artifacts.length" class="workspace-list-empty">Agent 结果可保存到这里</p>
          </section>
        </aside>

        <div
          class="workspace-resizer"
          :class="{ 'workspace-resizer--active': dragging === 'left' }"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整项目资料栏宽度"
          title="拖动调整宽度，双击恢复默认"
          tabindex="0"
          @pointerdown="startDrag('left', $event)"
          @pointermove="onDrag"
          @pointerup="endDrag"
          @pointercancel="endDrag"
          @dblclick="resetSplit('left')"
          @keydown.left.prevent="nudgeSplit('left', -16)"
          @keydown.right.prevent="nudgeSplit('left', 16)"
        ></div>

        <main class="workspace-editor workspace-pane" :class="{ 'is-active': activePane === 'document' }">
          <template v-if="activeDocument">
            <div class="editor-toolbar">
              <input v-model="activeDocument.title" :disabled="!canEdit" aria-label="文档标题" />
              <div><span :class="{ 'save-error': saveState.includes('失败') || saveState.includes('冲突') }">{{ saveState || `当前版本 v${activeDocument.version}` }}</span><button v-if="canEdit" class="primary-button" type="button" :disabled="saving" @click="saveDocument"><Save :size="15" />保存</button></div>
            </div>
            <textarea v-model="activeDocument.content" class="research-document-editor" :readonly="!canEdit" spellcheck="false" aria-label="Markdown 研究文档"></textarea>
            <footer class="editor-status"><span>Markdown</span><span>{{ activeDocument.content?.length || 0 }} 字符</span><span>版本历史已开启</span></footer>
          </template>
          <div v-else-if="documentLoading" class="editor-empty"><LoaderCircle class="spin" :size="22" />正在加载文档...</div>
          <div v-else class="editor-empty"><FileText :size="32" /><strong>项目文档区</strong><p>新建研究笔记，或选择左侧已有文档继续编辑。</p><button v-if="canEdit" class="primary-button" type="button" @click="createDocument"><Plus :size="16" />新建文档</button></div>
        </main>

        <div
          class="workspace-resizer"
          :class="{ 'workspace-resizer--active': dragging === 'right' }"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整项目助手栏宽度"
          title="拖动调整宽度，双击恢复默认"
          tabindex="0"
          @pointerdown="startDrag('right', $event)"
          @pointermove="onDrag"
          @pointerup="endDrag"
          @pointercancel="endDrag"
          @dblclick="resetSplit('right')"
          @keydown.left.prevent="nudgeSplit('right', -16)"
          @keydown.right.prevent="nudgeSplit('right', 16)"
        ></div>

        <aside class="workspace-assistant workspace-pane" :class="{ 'is-active': activePane === 'assistant' }">
          <header class="assistant-header"><div><span><ZyIcon name="message" :size="18" /></span><div><strong>项目助手</strong><small>了解这个项目的文档和任务</small></div></div><button v-if="canEdit" class="icon-button" type="button" aria-label="新建对话" title="新建对话" @click="createConversation"><Plus :size="16" /></button></header>
          <label v-if="workspace.conversations.length > 1" class="conversation-select"><MessageSquareText :size="14" /><select v-model="activeConversationId"><option v-for="item in workspace.conversations" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
          <TaskComposer v-if="activeConversationId && canEdit" :project-id="projectId" :conversation-id="activeConversationId" @completed="refreshWorkspaceLists" />
          <div v-else-if="!canEdit" class="assistant-readonly"><MessageSquareText :size="24" /><strong>当前为只读权限</strong><p>你可以查看项目资产，但不能发送消息或创建任务。</p></div>
          <div v-else class="assistant-readonly"><LoaderCircle class="spin" :size="22" />正在建立项目对话...</div>
          <section class="workspace-agent-shortcuts"><header><span>智能体</span><RouterLink to="/agents">全部</RouterLink></header><div><RouterLink v-for="agent in agents.slice(0, 6)" :key="agent.id" :to="{ path: agent.route || '/agents', query: { project: projectId } }"><ZyIcon :name="agentIcon(agent)" :size="15" /><span>{{ agent.name }}</span></RouterLink></div></section>
        </aside>
      </div>
    </template>
  </div>
</template>
