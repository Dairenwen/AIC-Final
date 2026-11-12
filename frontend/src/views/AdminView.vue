<script setup lang="ts">
import { ArrowUpRight, ChartNoAxesCombined, Database, ShieldAlert, ShieldCheck, ScrollText } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const modules = [
  { name: '系统仪表盘', description: '平台用户、任务、模型与服务状态', to: '/admin/system/dashboard', icon: ChartNoAxesCombined },
  { name: '知识库仪表盘', description: '知识库索引、问答与数据同步状态', to: '/admin/system/knowledge-dashboard', icon: Database },
  { name: '异常监控', description: 'Web 系统任务失败、服务告警与重试记录', to: '/admin/system/exceptions', icon: ShieldAlert },
  { name: '审计日志', description: '用户操作、任务事件与安全审计记录', to: '/admin/system/audit', icon: ScrollText },
  { name: '权限管理', description: '系统角色、用户数量与访问范围', to: '/admin/system/permissions', icon: ShieldCheck },
]
</script>

<template>
  <div class="workspace-page admin-page">
    <PageHeader eyebrow="PLATFORM ADMINISTRATION" title="系统配置" description="统一管理 Web 系统运行状态、知识库服务与安全治理。">
      <span class="admin-role"><ShieldCheck :size="15" />系统管理员</span>
    </PageHeader>

    <section class="admin-config-intro">
      <div>
        <span class="admin-config-kicker">SYSTEM CONFIGURATION</span>
        <h2>系统配置</h2>
        <p>按平台资源边界进入对应管理工作区。</p>
      </div>
      <span class="admin-config-count">5 个管理模块</span>
    </section>

    <section class="admin-module-list admin-module-list--config">
      <button v-for="module in modules" :key="module.to" type="button" @click="router.push(module.to)">
        <span class="module-icon"><component :is="module.icon" :size="19" /></span>
        <span class="module-copy"><strong>{{ module.name }}</strong><small>{{ module.description }}</small></span>
        <ArrowUpRight :size="17" />
      </button>
    </section>
  </div>
</template>

<style scoped>
.admin-page{max-width:1380px}.admin-config-intro{margin-bottom:18px;padding:22px 24px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid var(--line)}.admin-config-kicker{color:var(--green-700);font-size:12px;font-weight:800}.admin-config-intro h2{margin:5px 0 4px;font-size:19px}.admin-config-intro p{margin:0;color:var(--muted);font-size:14px}.admin-config-count{color:var(--muted);font-size:13px}.admin-module-list--config{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid var(--line);border-radius:7px;overflow:hidden;background:#fff}.admin-module-list--config button{min-width:0;min-height:138px;padding:18px;display:grid;grid-template-columns:auto 1fr auto;align-items:start;gap:12px;text-align:left;background:#fff;border:0;border-right:1px solid var(--line)}.admin-module-list--config button:last-child{border-right:0}.admin-module-list--config button:hover{background:var(--surface-soft)}.module-icon{width:36px;height:36px;display:grid;place-items:center;color:var(--green-800);background:var(--green-100);border:1px solid #d8e7dd;border-radius:6px}.module-copy{min-width:0;display:flex;flex-direction:column;gap:7px}.module-copy strong{font-size:15px}.module-copy small{color:var(--muted);font-size:13px;line-height:1.6}.admin-module-list--config button>svg{color:var(--muted)}
@media(max-width:1100px){.admin-module-list--config{grid-template-columns:repeat(2,minmax(0,1fr))}.admin-module-list--config button{border-bottom:1px solid var(--line)}.admin-module-list--config button:nth-child(2n){border-right:0}.admin-module-list--config button:last-child{border-bottom:0}}
@media(max-width:620px){.admin-config-intro{align-items:flex-start;gap:16px}.admin-module-list--config{grid-template-columns:1fr}.admin-module-list--config button{min-height:100px;border-right:0}.admin-module-list--config button:last-child{border-bottom:0}}
</style>
