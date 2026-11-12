<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown, ArrowRight, ArrowUp, Bot, CheckCircle2, ChevronRight, CircleAlert,
  Clock3, Edit3, FileInput, LoaderCircle, Play, Plus, RotateCcw, Save, Sparkles,
  Trash2, UsersRound, X,
} from 'lucide-vue-next'

import { getData, http } from '@/api/http'
import PageHeader from '@/components/PageHeader.vue'
import type { AgentTeam, AgentTeamRun, AgentTeamTemplate, CatalogItem } from '@/types'
import { renderMarkdown } from '@/utils/renderMarkdown'

const router = useRouter()
const route = useRoute()
const teams = ref<AgentTeam[]>([])
const templates = ref<AgentTeamTemplate[]>([])
const agents = ref<CatalogItem[]>([])
const loading = ref(true)
const editorOpen = ref(false)
const editingId = ref<string | null>(null)
const formName = ref('')
const formDescription = ref('')
const formMembers = ref<string[]>([])
const formError = ref('')
const saving = ref(false)
const selectedTeam = ref<AgentTeam | null>(null)
const prompt = ref('')
const running = ref(false)
const runError = ref('')
const activeRun = ref<AgentTeamRun | null>(null)
let pollTimer: number | null = null

const agentByCode = computed(() => new Map(agents.value.map((item) => [item.code, item])))
const selectedMembers = computed(() => formMembers.value.map((code) => agentByCode.value.get(code)).filter(Boolean) as CatalogItem[])
const canSave = computed(() => formName.value.trim().length > 0 && formMembers.value.length >= 2 && formMembers.value.length <= 8 && !saving.value)
const runStages = computed(() => activeRun.value?.output.stages || [])
const renderedSummary = computed(() => renderMarkdown(activeRun.value?.output.final_summary || ''))

async function loadPage() {
  loading.value = true
  try {
    ;[teams.value, templates.value, agents.value] = await Promise.all([
      getData<AgentTeam[]>('/agent-teams'),
      getData<AgentTeamTemplate[]>('/agent-team-templates'),
      getData<CatalogItem[]>('/agents'),
    ])
    if (!selectedTeam.value && teams.value.length) selectedTeam.value = teams.value[0]
    const taskId = typeof route.query.task === 'string' ? route.query.task : ''
    if (taskId && activeRun.value?.id !== taskId) {
      activeRun.value = await getData<AgentTeamRun>(`/agent-team-runs/${taskId}`)
      selectedTeam.value = teams.value.find((item) => item.id === activeRun.value?.agent_team_id) || selectedTeam.value
      if (!['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(activeRun.value.status)) {
        running.value = true
        schedulePoll()
      }
    }
  } catch (error) {
    runError.value = requestError(error)
  } finally {
    loading.value = false
  }
}

function openCreate(template?: AgentTeamTemplate) {
  editingId.value = null
  formName.value = template?.name || ''
  formDescription.value = template?.description || ''
  formMembers.value = template?.members.map((item) => item.code) || []
  formError.value = ''
  editorOpen.value = true
}

function openEdit(team: AgentTeam) {
  editingId.value = team.id
  formName.value = team.name
  formDescription.value = team.description
  formMembers.value = team.members.map((item) => item.code)
  formError.value = ''
  editorOpen.value = true
}

function toggleMember(code?: string) {
  if (!code) return
  const index = formMembers.value.indexOf(code)
  if (index >= 0) formMembers.value.splice(index, 1)
  else if (formMembers.value.length < 8) formMembers.value.push(code)
}

function moveMember(index: number, direction: -1 | 1) {
  const target = index + direction
  if (target < 0 || target >= formMembers.value.length) return
  const copy = [...formMembers.value]
  ;[copy[index], copy[target]] = [copy[target], copy[index]]
  formMembers.value = copy
}

async function saveTeam() {
  if (!canSave.value) return
  saving.value = true
  formError.value = ''
  try {
    const payload = { name: formName.value.trim(), description: formDescription.value.trim(), members: formMembers.value }
    if (editingId.value) await http.patch(`/agent-teams/${editingId.value}`, payload)
    else await http.post('/agent-teams', payload)
    editorOpen.value = false
    await loadPage()
  } catch (error) {
    formError.value = requestError(error)
  } finally {
    saving.value = false
  }
}

async function deleteTeam(team: AgentTeam) {
  if (!window.confirm(`确认删除“${team.name}”吗？已有任务记录不会被删除。`)) return
  await http.delete(`/agent-teams/${team.id}`)
  if (selectedTeam.value?.id === team.id) selectedTeam.value = null
  await loadPage()
}

async function startRun() {
  if (!selectedTeam.value || prompt.value.trim().length < 10) {
    runError.value = '请至少输入 10 个字符的科研目标'
    return
  }
  running.value = true
  runError.value = ''
  activeRun.value = null
  try {
    const response = await http.post(`/agent-teams/${selectedTeam.value.id}/runs`, { prompt: prompt.value.trim(), model: 'vertical_domain' })
    activeRun.value = response.data.data as AgentTeamRun
    schedulePoll()
  } catch (error) {
    runError.value = requestError(error)
    running.value = false
  }
}

function schedulePoll() {
  stopPoll()
  pollTimer = window.setInterval(loadRun, 1000)
  void loadRun()
}

async function loadRun() {
  if (!activeRun.value) return
  try {
    activeRun.value = await getData<AgentTeamRun>(`/agent-team-runs/${activeRun.value.id}`)
    if (['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(activeRun.value.status)) {
      running.value = false
      stopPoll()
    }
  } catch (error) {
    runError.value = requestError(error)
    running.value = false
    stopPoll()
  }
}

function stopPoll() {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  pollTimer = null
}

function chooseTeam(team: AgentTeam) {
  selectedTeam.value = team
  activeRun.value = null
  runError.value = ''
  stopPoll()
}

function openStage(stage: { task_id?: string | null; route?: string }) {
  if (stage.task_id) {
    const agent = agentByCode.value.get((stage as { code?: string }).code)
    const route = agent?.route || stage.route
    if (route) void router.push({ path: route, query: { task: stage.task_id } })
    return
  }
  if (stage.route) void router.push(stage.route)
}

function stageLabel(status: string) {
  return ({ QUEUED: '排队中', RUNNING: '执行中', SUCCEEDED: '已完成', FAILED: '失败', WAITING_INPUT: '等待材料' } as Record<string, string>)[status] || status
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '请求失败，请稍后重试'
}

onMounted(loadPage)
onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="workspace-page team-page">
    <PageHeader eyebrow="MULTI-AGENT COLLABORATION" title="我的智囊团" description="组织多个专业 Agent，在同一科研目标下连续协作并交接阶段结果。">
      <button class="primary-button" type="button" @click="openCreate()"><Plus :size="15" />创建智囊团</button>
    </PageHeader>

    <section class="team-template-section">
      <div class="team-section-heading"><div><span>快速开始</span><h2>预置协作方案</h2></div><p>基于当前已接入的 Agent 组合，可在创建后继续调整顺序。</p></div>
      <div class="team-template-grid">
        <article v-for="template in templates" :key="template.id" class="team-template-card">
          <div class="team-template-card__top"><span class="team-template-icon"><Sparkles :size="18" /></span><span>{{ template.accent }}</span></div>
          <h3>{{ template.name }}</h3><p>{{ template.description }}</p>
          <div class="team-member-flow"><template v-for="(member, index) in template.members" :key="member.code"><span>{{ member.name }}</span><ChevronRight v-if="index < template.members.length - 1" :size="13" /></template></div>
          <button class="secondary-button" type="button" @click="openCreate(template)"><Plus :size="14" />使用此方案</button>
        </article>
      </div>
    </section>

    <section class="team-workbench">
      <aside class="team-list-panel">
        <div class="team-panel-title"><div><span>我的配置</span><strong>{{ teams.length }} 个智囊团</strong></div><button class="icon-button" type="button" title="刷新" aria-label="刷新" @click="loadPage"><RotateCcw :size="15" /></button></div>
        <div v-if="loading" class="team-list-loading"><LoaderCircle class="spin" :size="20" />正在加载</div>
        <div v-else-if="!teams.length" class="team-list-empty"><UsersRound :size="26" /><strong>尚未创建智囊团</strong><span>从上方方案开始，或自行选择成员。</span></div>
        <button v-for="team in teams" v-else :key="team.id" class="team-list-item" :class="{ active: selectedTeam?.id === team.id }" type="button" @click="chooseTeam(team)">
          <span class="team-list-item__icon"><UsersRound :size="17" /></span><span class="team-list-item__copy"><strong>{{ team.name }}</strong><small>{{ team.members.length }} 个 Agent · 顺序协作</small></span><ChevronRight :size="15" />
        </button>
      </aside>

      <div class="team-run-panel">
        <div v-if="selectedTeam" class="team-run-header">
          <div><span class="team-kicker">超级 Agent</span><h2>{{ selectedTeam.name }}</h2><p>{{ selectedTeam.description || '围绕同一科研目标组织多个 Agent 协作。' }}</p></div>
          <div v-if="selectedTeam.editable" class="team-run-actions"><button class="icon-button" title="编辑" aria-label="编辑" @click="openEdit(selectedTeam)"><Edit3 :size="15" /></button><button class="icon-button danger" title="删除" aria-label="删除" @click="deleteTeam(selectedTeam)"><Trash2 :size="15" /></button></div>
        </div>
        <div v-if="selectedTeam" class="team-active-flow">
          <template v-for="(member, index) in selectedTeam.members" :key="member.code"><div class="team-active-member" :class="{ 'requires-input': member.requires_input }"><span><Bot :size="15" /></span><strong>{{ member.name }}</strong><small>{{ member.requires_input ? '需专属材料' : '自动执行' }}</small></div><ArrowRight v-if="index < selectedTeam.members.length - 1" :size="16" /></template>
        </div>
        <div v-if="selectedTeam" class="team-goal-box">
          <label for="team-goal">本次科研目标</label><textarea id="team-goal" v-model="prompt" rows="5" placeholder="例如：系统调研多智能体协作在学术研究中的应用，识别近三年的关键方向和研究空白，并形成一份可用于开题的结构化初稿。"></textarea>
          <div><span><Clock3 :size="14" />成员按顺序执行，上一阶段结果会自动交接给下一阶段。</span><button class="primary-button" type="button" :disabled="running || prompt.trim().length < 10" @click="startRun"><LoaderCircle v-if="running" class="spin" :size="15" /><Play v-else :size="15" />{{ running ? '协作执行中' : '启动智囊团' }}</button></div>
          <p v-if="runError" class="team-error"><CircleAlert :size="14" />{{ runError }}</p>
        </div>
        <div v-if="!selectedTeam" class="team-run-empty"><UsersRound :size="32" /><h2>选择或创建一个智囊团</h2><p>配置成员后，可以输入科研目标并查看完整协作过程。</p></div>

        <section v-if="activeRun" class="team-run-result" aria-live="polite">
          <header><div><span>执行过程</span><h3>{{ activeRun.current_step || '等待启动' }}</h3></div><strong :class="`status-${activeRun.status.toLowerCase()}`">{{ stageLabel(activeRun.status) }}</strong></header>
          <div class="team-progress"><span :style="{ width: `${activeRun.progress}%` }"></span></div>
          <div class="team-timeline">
            <article v-for="(stage, index) in runStages" :key="`${stage.code}-${index}`" :class="`stage-${stage.status.toLowerCase()}`">
              <span class="team-stage-index"><CheckCircle2 v-if="stage.status === 'SUCCEEDED'" :size="16" /><FileInput v-else-if="stage.status === 'WAITING_INPUT'" :size="16" /><LoaderCircle v-else-if="stage.status === 'RUNNING'" class="spin" :size="16" /><span v-else>{{ index + 1 }}</span></span>
              <div><strong>{{ stage.name }}</strong><p>{{ stage.message || stageLabel(stage.status) }}</p></div>
              <button v-if="stage.task_id || stage.route" class="text-button" type="button" @click="openStage(stage)">{{ stage.status === 'WAITING_INPUT' ? '补充材料' : '查看结果' }}<ChevronRight :size="13" /></button>
            </article>
          </div>
          <article v-if="activeRun.output.final_summary" class="team-final-summary"><div class="team-final-summary__title"><Sparkles :size="16" /><strong>智囊团综合结论</strong></div><div class="markdown-body" v-html="renderedSummary"></div></article>
          <p v-if="activeRun.error" class="team-error"><CircleAlert :size="14" />{{ activeRun.error }}</p>
        </section>
      </div>
    </section>

    <div v-if="editorOpen" class="team-editor-backdrop" @click.self="editorOpen = false">
      <section class="team-editor" role="dialog" aria-modal="true" aria-labelledby="team-editor-title">
        <header><div><span>SUPER AGENT BUILDER</span><h2 id="team-editor-title">{{ editingId ? '编辑智囊团' : '创建智囊团' }}</h2></div><button class="icon-button" title="关闭" aria-label="关闭" @click="editorOpen = false"><X :size="17" /></button></header>
        <div class="team-editor-body">
          <label class="team-form-field"><span>名称</span><input v-model="formName" maxlength="80" placeholder="例如：领域调研智囊团" /></label>
          <label class="team-form-field"><span>说明</span><textarea v-model="formDescription" rows="3" maxlength="500" placeholder="说明适用场景和期望产出"></textarea></label>
          <div class="team-member-picker"><div><span>选择成员</span><small>已选择 {{ formMembers.length }}/8，至少 2 个</small></div><div class="team-agent-options"><button v-for="agent in agents" :key="agent.id" type="button" :class="{ selected: formMembers.includes(agent.code || '') }" @click="toggleMember(agent.code)"><span><Bot :size="15" /></span><strong>{{ agent.name }}</strong><small>{{ agent.category }}</small></button></div></div>
          <div class="team-order-list"><div><span>协作顺序</span><small>上一成员的结果会作为下一成员的上下文</small></div><p v-if="!selectedMembers.length">请先选择成员</p><article v-for="(member, index) in selectedMembers" :key="member.id"><span>{{ index + 1 }}</span><div><strong>{{ member.name }}</strong><small>{{ member.description }}</small></div><button class="icon-button" type="button" :disabled="index === 0" title="上移" aria-label="上移" @click="moveMember(index, -1)"><ArrowUp :size="14" /></button><button class="icon-button" type="button" :disabled="index === selectedMembers.length - 1" title="下移" aria-label="下移" @click="moveMember(index, 1)"><ArrowDown :size="14" /></button></article></div>
          <p v-if="formError" class="team-error"><CircleAlert :size="14" />{{ formError }}</p>
        </div>
        <footer><button class="secondary-button" type="button" @click="editorOpen = false">取消</button><button class="primary-button" type="button" :disabled="!canSave" @click="saveTeam"><LoaderCircle v-if="saving" class="spin" :size="15" /><Save v-else :size="15" />保存智囊团</button></footer>
      </section>
    </div>
  </div>
</template>
