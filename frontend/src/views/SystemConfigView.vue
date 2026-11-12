<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Activity, AlertTriangle, BarChart3, CheckCircle2, Database, RefreshCw, ScrollText, ShieldAlert, ShieldCheck, Users, XCircle } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import { getData } from '@/api/http'
import PageHeader from '@/components/PageHeader.vue'

interface Metric { label: string; value: number; trend: string }
interface Overview { metrics: Metric[]; summary: { activeAgents: number; activeTools: number; lastRefresh: string }; alerts: Array<{ level: string; message: string }>; components: Record<string, string> }
interface ExceptionItem { id: string; type: string; status: string; message: string; createdAt: string | null; retryCount: number }
interface AuditItem { id: string; resource: string; resourceId: string; action: string; detail: Record<string, unknown>; createdAt: string | null }
interface PermissionItem { code: string; name: string; description: string; status: string; userCount: number }

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const overview = ref<Overview | null>(null)
const exceptions = ref<ExceptionItem[]>([])
const audit = ref<AuditItem[]>([])
const permissions = ref<PermissionItem[]>([])

const tabs = [
  { key: 'dashboard', label: '系统仪表盘', icon: BarChart3 },
  { key: 'knowledge-dashboard', label: '知识库仪表盘', icon: Database },
  { key: 'exceptions', label: '异常监控', icon: ShieldAlert },
  { key: 'audit', label: '审计日志', icon: ScrollText },
  { key: 'permissions', label: '权限管理', icon: ShieldCheck },
] as const

const section = computed(() => String(route.params.section || 'dashboard'))
const title = '系统配置'
const description = '统一管理 Web 系统运行状态、知识库服务与安全治理。'

function selectTab(key: string) {
  if (key !== section.value) void router.push(`/admin/system/${key}`)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (section.value === 'dashboard') overview.value = await getData<Overview>('/admin/system/dashboard')
    if (section.value === 'exceptions') exceptions.value = (await getData<{ items: ExceptionItem[] }>('/admin/system/exceptions')).items
    if (section.value === 'audit') audit.value = (await getData<{ items: AuditItem[] }>('/admin/system/audit')).items
    if (section.value === 'permissions') permissions.value = (await getData<{ items: PermissionItem[] }>('/admin/system/permissions')).items
  } catch {
    error.value = '系统管理数据暂时不可用，请稍后刷新。'
  } finally { loading.value = false }
}
watch(section, () => void load(), { immediate: true })
const formatDate = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
</script>

<template>
  <div class="workspace-page system-config-page">
    <PageHeader eyebrow="SYSTEM CONFIGURATION" :title="title" :description="description">
      <button class="admin-refresh-button" type="button" title="刷新数据" aria-label="刷新数据" @click="load"><RefreshCw :size="16" :class="{ 'spin-once': loading }" /></button>
    </PageHeader>

    <nav class="system-tabs" aria-label="系统配置分类">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="system-tab"
        :class="{ 'system-tab--active': section === tab.key }"
        :aria-current="section === tab.key ? 'page' : undefined"
        @click="selectTab(tab.key)"
      >
        <component :is="tab.icon" :size="17" />
        <span>{{ tab.label }}</span>
      </button>
    </nav>

    <div v-if="loading" class="system-state"><Activity :size="18" class="system-spin" />正在加载系统数据</div>
    <div v-else-if="error" class="system-state system-state--error"><XCircle :size="18" />{{ error }}</div>

    <template v-else-if="section === 'dashboard' && overview">
      <section class="system-metrics">
        <article v-for="metric in overview.metrics" :key="metric.label"><small>{{ metric.label }}</small><strong>{{ metric.value.toLocaleString() }}</strong><em>{{ metric.trend }}</em></article>
      </section>
      <section class="system-grid">
        <article class="system-panel"><h2>服务状态</h2><div class="status-row"><CheckCircle2 :size="17" /><span>API 服务</span><strong>正常</strong></div><div class="status-row"><CheckCircle2 :size="17" /><span>数据库</span><strong>正常</strong></div><div class="status-row"><CheckCircle2 :size="17" /><span>Agent {{ overview.summary.activeAgents }} 个</span><strong>已启用</strong></div></article>
        <article class="system-panel"><h2>运行提醒</h2><p v-for="alert in overview.alerts" :key="alert.message" class="notice-row"><AlertTriangle :size="16" />{{ alert.message }}</p><p v-if="!overview.alerts.length" class="notice-row notice-row--ok"><CheckCircle2 :size="16" />当前没有待处理系统异常</p></article>
      </section>
    </template>

    <section v-else-if="section === 'exceptions'" class="system-panel system-table-panel"><div class="panel-heading"><h2>Web 系统异常</h2><span>{{ exceptions.length }} 条记录</span></div><div v-if="!exceptions.length" class="empty-state"><CheckCircle2 :size="22" />当前没有失败任务</div><table v-else><thead><tr><th>任务类型</th><th>状态</th><th>异常信息</th><th>重试次数</th><th>发生时间</th></tr></thead><tbody><tr v-for="item in exceptions" :key="item.id"><td>{{ item.type }}</td><td><span class="status-badge status-badge--danger"><XCircle :size="13" />{{ item.status }}</span></td><td>{{ item.message }}</td><td>{{ item.retryCount }}</td><td>{{ formatDate(item.createdAt) }}</td></tr></tbody></table></section>
    <section v-else-if="section === 'audit'" class="system-panel system-table-panel"><div class="panel-heading"><h2>Web 系统审计日志</h2><span>{{ audit.length }} 条记录</span></div><div v-if="!audit.length" class="empty-state"><ScrollText :size="22" />暂无审计事件</div><table v-else><thead><tr><th>资源</th><th>操作</th><th>详情</th><th>时间</th></tr></thead><tbody><tr v-for="item in audit" :key="item.id"><td>{{ item.resource }} / {{ item.resourceId.slice(0, 8) }}</td><td>{{ item.action }}</td><td>{{ JSON.stringify(item.detail) }}</td><td>{{ formatDate(item.createdAt) }}</td></tr></tbody></table></section>
    <section v-else-if="section === 'permissions'" class="system-panel system-table-panel"><div class="panel-heading"><h2>系统角色与权限</h2><span>{{ permissions.length }} 个角色</span></div><table><thead><tr><th>角色</th><th>说明</th><th>用户数</th><th>状态</th></tr></thead><tbody><tr v-for="item in permissions" :key="item.code"><td><strong>{{ item.name }}</strong><small>{{ item.code }}</small></td><td>{{ item.description || '系统访问角色' }}</td><td>{{ item.userCount }}</td><td><span class="status-badge status-badge--ok"><ShieldCheck :size="13" />{{ item.status }}</span></td></tr></tbody></table><div v-if="!permissions.length" class="empty-state"><Users :size="22" />暂无角色数据</div></section>

    <section v-else-if="section === 'knowledge-dashboard'" class="knowledge-dashboard-shell"><iframe src="/api/v1/knowledge-base/ui?embed=1&tab=dashboard" title="知识库仪表盘" /></section>
  </div>
</template>

<style scoped>
.system-config-page{max-width:1440px}.admin-refresh-button{width:34px;height:34px;display:grid;place-items:center;color:var(--green-700);background:#fff;border:1px solid var(--line);border-radius:5px}.system-tabs{display:flex;gap:8px;margin:0 0 22px;padding-bottom:1px;overflow-x:auto}.system-tab{min-height:54px;min-width:170px;padding:0 18px;display:inline-flex;align-items:center;justify-content:center;gap:9px;color:var(--muted);background:#fff;border:1px solid var(--line);border-radius:6px;font-size:15px;white-space:nowrap;transition:140ms ease}.system-tab:hover{color:var(--ink);border-color:var(--green-700);background:var(--surface-soft)}.system-tab--active{color:var(--green-800);border-color:var(--green-700);background:#f2f7f3;font-weight:700;box-shadow:inset 0 -2px 0 var(--green-700)}.system-tab svg{color:var(--green-700)}.system-state{min-height:420px;display:flex;align-items:center;justify-content:center;gap:9px;color:var(--muted)}.system-state--error{color:var(--danger)}.system-spin{animation:system-spin .9s linear infinite}.system-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);border-radius:7px;background:#fff;overflow:hidden}.system-metrics article{min-height:112px;padding:22px;display:grid;grid-template-columns:1fr auto;gap:5px 12px;border-right:1px solid var(--line)}.system-metrics article:last-child{border-right:0}.system-metrics small{grid-column:1/-1;color:var(--muted)}.system-metrics strong{font-size:25px}.system-metrics em{align-self:end;color:var(--green-700);font-size:13px;font-style:normal}.system-grid{margin-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:18px}.system-panel{padding:20px;background:#fff;border:1px solid var(--line);border-radius:7px}.system-panel h2{margin:0 0 16px;font-size:16px}.status-row,.notice-row{min-height:42px;margin:0;display:flex;align-items:center;gap:9px;border-top:1px solid var(--line);color:var(--muted)}.status-row:first-of-type{border-top:0}.status-row svg,.notice-row--ok svg{color:var(--success)}.status-row span{flex:1}.status-row strong{color:var(--ink);font-size:14px}.notice-row svg{color:var(--warning)}.system-table-panel{padding:0;overflow:hidden}.panel-heading{min-height:68px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}.panel-heading h2{margin:0}.panel-heading span{color:var(--muted);font-size:14px}.system-table-panel table{width:100%;border-collapse:collapse}.system-table-panel th,.system-table-panel td{padding:13px 16px;text-align:left;border-bottom:1px solid var(--line);font-size:14px;vertical-align:middle}.system-table-panel th{color:var(--muted);background:var(--surface-soft);font-size:13px}.system-table-panel td{max-width:420px;color:var(--ink)}.system-table-panel td small{margin-top:3px;display:block;color:var(--muted)}.status-badge{width:max-content;min-height:24px;padding:0 8px;display:inline-flex;align-items:center;gap:5px;border-radius:4px;font-size:13px;font-weight:700}.status-badge--danger{color:var(--danger);background:#fff0ef}.status-badge--ok{color:var(--green-700);background:var(--green-100)}.empty-state{min-height:260px;display:flex;align-items:center;justify-content:center;gap:9px;color:var(--muted)}.knowledge-dashboard-shell{height:calc(100vh - 250px);min-height:620px;margin:-8px -4px 0;overflow:hidden;border:1px solid var(--line);border-radius:7px;background:#eef1ef}.knowledge-dashboard-shell iframe{width:100%;height:100%;display:block;border:0}.spin-once{animation:system-spin .9s linear infinite}@keyframes system-spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.system-tabs{margin-bottom:18px}.system-tab{min-width:150px}.system-metrics{grid-template-columns:1fr 1fr}.system-metrics article:nth-child(2){border-right:0}.system-metrics article:nth-child(-n+2){border-bottom:1px solid var(--line)}.system-grid{grid-template-columns:1fr}.system-table-panel{overflow-x:auto}.system-table-panel table{min-width:720px}.knowledge-dashboard-shell{height:calc(100vh - 230px);min-height:560px}}
@media(max-width:560px){.system-tabs{margin-right:-16px;padding-right:16px}.system-tab{min-width:148px;min-height:48px;padding:0 14px;font-size:15px}.system-metrics{grid-template-columns:1fr}.system-metrics article{border-right:0;border-bottom:1px solid var(--line)}.system-metrics article:last-child{border-bottom:0}.knowledge-dashboard-shell{margin:0;height:calc(100vh - 230px)}}
</style>
