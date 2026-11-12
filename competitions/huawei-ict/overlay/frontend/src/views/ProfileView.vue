<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { LoaderCircle } from 'lucide-vue-next'

import { getData } from '@/api/http'
import { changePassword } from '@/auth/session'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import PageHeader from '@/components/PageHeader.vue'
import ModelLibraryView from '@/views/ModelLibraryView.vue'
import type { Profile, TokenUsageSnapshot } from '@/types'

const profile = ref<Profile>({
  id: '',
  name: '',
  organization: '',
  role: 'normal_user',
  plan: '科研基础版',
  modelConfigured: false,
})
const route = useRoute()
const usage = ref<TokenUsageSnapshot>({ period_start: '', period_end: '', quota: 1000000, used: 0, remaining: 1000000, unlimited: false, usage_percent: 0, breakdown: { platform: 0, api: 0 } })
const activeTab = ref<'profile' | 'model' | 'password' | 'plan'>(route.query.tab === 'model' ? 'model' : 'profile')
const passwordForm = ref({ current: '', next: '', confirm: '' })
const passwordBusy = ref(false)
const passwordError = ref('')
const passwordNotice = ref('')
const canChangePassword = computed(() => {
  const form = passwordForm.value
  return Boolean(form.current && form.next && form.confirm) && !passwordBusy.value
})

async function submitPassword() {
  const form = passwordForm.value
  passwordError.value = ''
  passwordNotice.value = ''
  if (form.next.length < 8) {
    passwordError.value = '新密码至少需要 8 个字符'
    return
  }
  if (form.next !== form.confirm) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordBusy.value = true
  try {
    await changePassword(form.current, form.next)
    passwordForm.value = { current: '', next: '', confirm: '' }
    passwordNotice.value = '密码已更新，其他设备需要重新登录'
  } catch (error) {
    const value = error as { response?: { data?: { error?: { message?: string } } } }
    passwordError.value = value.response?.data?.error?.message || '密码没有更新，请稍后再试'
  } finally {
    passwordBusy.value = false
  }
}

const usageWidth = computed(() => `${Math.min(100, Math.max(0, usage.value.usage_percent))}%`)

function formatTokens(value: number | null) {
  if (value === null) return '不限'
  if (value >= 1000000) return `${(value / 1000000).toFixed(value % 1000000 ? 2 : 0)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(value % 1000 ? 1 : 0)}K`
  return String(value)
}

onMounted(async () => {
  try {
    profile.value = await getData<Profile>('/users/me')
  } catch {
    // Keep the page usable when profile metadata is temporarily unavailable.
  }
  try {
    usage.value = await getData<TokenUsageSnapshot>('/model-usage')
  } catch {
    // Older deployments may not have run the token-usage migration yet.
  }
})
</script>

<template>
  <div class="workspace-page profile-page">
    <PageHeader title="个人中心" description="账号资料、模型接口和用量。" />

    <div class="profile-layout">
      <aside class="profile-nav">
        <div class="profile-identity">
          <span class="profile-avatar">{{ (profile.name || '研').slice(0, 1) }}</span>
          <div><strong>{{ profile.name }}</strong><small>{{ profile.organization }}</small></div>
        </div>
        <button :class="{ active: activeTab === 'profile' }" type="button" @click="activeTab = 'profile'"><ZyIcon name="user" :size="17" />个人资料</button>
        <button :class="{ active: activeTab === 'model' }" type="button" @click="activeTab = 'model'"><ZyIcon name="key" :size="17" />模型库</button>
        <button :class="{ active: activeTab === 'password' }" type="button" @click="activeTab = 'password'"><ZyIcon name="lock" :size="17" />修改密码</button>
        <button :class="{ active: activeTab === 'plan' }" type="button" @click="activeTab = 'plan'"><ZyIcon name="gauge" :size="17" />用量</button>
      </aside>

      <Transition name="swap" mode="out-in">
        <section :key="activeTab" class="profile-panel" :class="{ 'profile-panel--models': activeTab === 'model' }">
          <template v-if="activeTab === 'profile'">
            <div class="panel-heading"><div><h2>个人资料</h2><p>这些信息来自注册时的填写，目前不能在这里修改。</p></div></div>
            <div class="form-grid">
              <label><span>姓名</span><input :value="profile.name" disabled /></label>
              <label><span>学校或机构</span><input :value="profile.organization" disabled /></label>
              <label><span>角色</span><input :value="profile.role === 'system_admin' ? '系统管理员' : '普通用户'" disabled /></label>
              <label><span>账号 ID</span><input :value="profile.id" disabled /></label>
            </div>
          </template>

          <template v-else-if="activeTab === 'model'">
            <ModelLibraryView embedded />
          </template>

          <template v-else-if="activeTab === 'password'">
            <div class="panel-heading"><div><h2>修改密码</h2><p>修改后，其他已登录的设备需要重新登录。</p></div></div>
            <form class="form-stack" @submit.prevent="submitPassword">
              <label><span>当前密码</span><input v-model="passwordForm.current" type="password" autocomplete="current-password" /></label>
              <label><span>新密码</span><input v-model="passwordForm.next" type="password" autocomplete="new-password" placeholder="至少 8 位" /></label>
              <label><span>确认新密码</span><input v-model="passwordForm.confirm" type="password" autocomplete="new-password" /></label>
              <p v-if="passwordError" class="inline-error" role="alert">{{ passwordError }}</p>
              <p v-if="passwordNotice" class="inline-note" role="status">{{ passwordNotice }}</p>
              <div class="form-actions">
                <button class="primary-button" type="submit" :disabled="!canChangePassword">
                  <LoaderCircle v-if="passwordBusy" class="spin" :size="15" />更新密码
                </button>
              </div>
            </form>
          </template>

          <template v-else>
            <div class="panel-heading"><div><h2>用量</h2><p>本周期内模型消耗的 Token。</p></div><span v-if="profile.plan" class="plan-badge">{{ profile.plan }}</span></div>
            <div class="quota-list">
              <div>
                <span>
                  <strong>模型 Token</strong>
                  <small>已用 {{ formatTokens(usage.used) }}，额度 {{ usage.unlimited ? '不限' : formatTokens(usage.quota) }}</small>
                </span>
                <div><i :style="{ width: usageWidth }"></i></div>
              </div>
            </div>
            <dl class="usage-breakdown">
              <div><dt>平台模型</dt><dd>{{ formatTokens(usage.breakdown.platform) }}</dd></div>
              <div><dt>自有接口</dt><dd>{{ formatTokens(usage.breakdown.api) }}</dd></div>
            </dl>
          </template>
        </section>
      </Transition>
    </div>
  </div>
</template>
