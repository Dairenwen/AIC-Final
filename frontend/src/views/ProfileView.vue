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
const usagePercentLabel = computed(() => (usage.value.unlimited ? '不限额' : `${Math.round(Math.min(100, Math.max(0, usage.value.usage_percent)))}%`))
const usageTone = computed(() => (usage.value.unlimited ? 'normal' : usage.value.usage_percent >= 90 ? 'danger' : usage.value.usage_percent >= 70 ? 'warning' : 'normal'))
const roleLabel = computed(() => (profile.value.role === 'system_admin' ? '系统管理员' : '普通用户'))
// Show the phone number the way account pages usually do: middle digits hidden.
const maskedPhone = computed(() => {
  const value = (profile.value.phone || '').replace(/^\+86/, '')
  if (value.length < 7) return value || '未绑定'
  return `${value.slice(0, 3)} **** ${value.slice(-4)}`
})
const periodLabel = computed(() => {
  const { period_start: start, period_end: end } = usage.value
  if (!start || !end) return '本周期'
  const format = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' })
  return `${format.format(new Date(start))} – ${format.format(new Date(end))}`
})
const breakdownTotal = computed(() => Math.max(1, usage.value.breakdown.platform + usage.value.breakdown.api))
const platformShare = computed(() => `${(usage.value.breakdown.platform / breakdownTotal.value) * 100}%`)

const tabs = [
  { key: 'profile', icon: 'user', label: '个人资料', hint: '姓名、机构与账号信息' },
  { key: 'model', icon: 'key', label: '模型库', hint: '平台模型与自有接口' },
  { key: 'password', icon: 'lock', label: '修改密码', hint: '登录密码与账号安全' },
  { key: 'plan', icon: 'gauge', label: '用量', hint: '本周期 Token 消耗' },
] as const

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

    <section class="pc-summary" aria-label="账号概览">
      <div class="pc-summary__identity">
        <span class="pc-avatar" aria-hidden="true">{{ (profile.name || '研').slice(0, 1) }}</span>
        <div class="pc-summary__text">
          <div class="pc-summary__name">
            <h2>{{ profile.name || '未命名用户' }}</h2>
            <span class="pc-badge" :class="{ 'pc-badge--accent': profile.role === 'system_admin' }">{{ roleLabel }}</span>
            <span v-if="profile.plan" class="pc-badge">{{ profile.plan }}</span>
          </div>
          <p>
            <span><ZyIcon name="building" :size="14" />{{ profile.organization || '未设置机构' }}</span>
            <span><ZyIcon name="phone" :size="14" />{{ maskedPhone }}</span>
          </p>
        </div>
      </div>
      <button class="pc-summary__usage" type="button" @click="activeTab = 'plan'">
        <span class="pc-summary__usage-head"><small>本周期用量</small><b>{{ usagePercentLabel }}</b></span>
        <span class="pc-meter" :class="`pc-meter--${usageTone}`"><i :style="{ width: usage.unlimited ? '0%' : usageWidth }"></i></span>
        <small>已用 {{ formatTokens(usage.used) }} / {{ usage.unlimited ? '不限' : formatTokens(usage.quota) }}</small>
      </button>
    </section>

    <div class="profile-layout pc-layout">
      <nav class="pc-nav" aria-label="个人中心">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="{ active: activeTab === tab.key }"
          type="button"
          :aria-current="activeTab === tab.key ? 'page' : undefined"
          @click="activeTab = tab.key"
        >
          <ZyIcon :name="tab.icon" :size="17" />
          <span><strong>{{ tab.label }}</strong><small>{{ tab.hint }}</small></span>
        </button>
      </nav>

      <Transition name="swap" mode="out-in">
        <section :key="activeTab" class="profile-panel pc-panel" :class="{ 'profile-panel--models': activeTab === 'model' }">
          <template v-if="activeTab === 'profile'">
            <header class="pc-panel__header">
              <h2>个人资料</h2>
              <p>这些信息来自注册时的填写，目前不能在这里修改。</p>
            </header>
            <dl class="pc-rows">
              <div><dt><strong>姓名</strong><small>在项目协作和对话中显示</small></dt><dd>{{ profile.name || '—' }}</dd></div>
              <div><dt><strong>学校或机构</strong><small>注册时填写的单位</small></dt><dd>{{ profile.organization || '未设置机构' }}</dd></div>
              <div><dt><strong>手机号</strong><small>用于登录和找回密码</small></dt><dd>{{ maskedPhone }}</dd></div>
              <div><dt><strong>角色</strong><small>决定能看到哪些管理功能</small></dt><dd><span class="pc-badge" :class="{ 'pc-badge--accent': profile.role === 'system_admin' }">{{ roleLabel }}</span></dd></div>
              <div><dt><strong>套餐</strong><small>决定每个周期的 Token 额度</small></dt><dd>{{ profile.plan || '—' }}</dd></div>
              <div><dt><strong>账号 ID</strong><small>反馈问题时可提供给管理员</small></dt><dd><code>{{ profile.id || '—' }}</code></dd></div>
            </dl>
          </template>

          <template v-else-if="activeTab === 'model'">
            <ModelLibraryView embedded />
          </template>

          <template v-else-if="activeTab === 'password'">
            <header class="pc-panel__header">
              <h2>修改密码</h2>
              <p>修改后，其他已登录的设备需要重新登录。</p>
            </header>
            <div class="pc-password">
              <form class="form-stack pc-form" @submit.prevent="submitPassword">
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
              <aside class="pc-note">
                <strong>密码要求</strong>
                <ul>
                  <li>至少 8 个字符</li>
                  <li>两次输入的新密码需要一致</li>
                  <li>建议不要与其他网站使用相同密码</li>
                </ul>
              </aside>
            </div>
          </template>

          <template v-else>
            <header class="pc-panel__header pc-panel__header--row">
              <div>
                <h2>用量</h2>
                <p>本周期内模型消耗的 Token。统计周期：{{ periodLabel }}</p>
              </div>
              <span v-if="profile.plan" class="pc-badge">{{ profile.plan }}</span>
            </header>
            <div class="pc-stats">
              <div><small>已用</small><b>{{ formatTokens(usage.used) }}</b></div>
              <div><small>额度</small><b>{{ usage.unlimited ? '不限' : formatTokens(usage.quota) }}</b></div>
              <div><small>剩余</small><b>{{ usage.unlimited ? '不限' : formatTokens(usage.remaining) }}</b></div>
              <div><small>使用比例</small><b>{{ usagePercentLabel }}</b></div>
            </div>
            <div class="pc-block">
              <div class="pc-block__head"><strong>模型 Token</strong><small>已用 {{ formatTokens(usage.used) }}，额度 {{ usage.unlimited ? '不限' : formatTokens(usage.quota) }}</small></div>
              <span class="pc-meter pc-meter--large" :class="`pc-meter--${usageTone}`"><i :style="{ width: usage.unlimited ? '0%' : usageWidth }"></i></span>
            </div>
            <div class="pc-block">
              <div class="pc-block__head"><strong>消耗来源</strong><small>平台模型与自有接口各自的消耗</small></div>
              <span class="pc-split" aria-hidden="true"><i :style="{ width: platformShare }"></i></span>
              <dl class="pc-legend">
                <div><dt><i class="pc-legend__dot pc-legend__dot--platform"></i>平台模型</dt><dd>{{ formatTokens(usage.breakdown.platform) }}</dd></div>
                <div><dt><i class="pc-legend__dot"></i>自有接口</dt><dd>{{ formatTokens(usage.breakdown.api) }}</dd></div>
              </dl>
            </div>
          </template>
        </section>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
/* ---- Account summary ------------------------------------------------- */
.pc-summary {
  display: flex;
  margin-top: 0;
  padding: var(--space-5) var(--space-6);
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-card);
}

.pc-summary__identity {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-4);
}

.pc-avatar {
  display: grid;
  width: 3.5rem;
  height: 3.5rem;
  flex: 0 0 auto;
  place-items: center;
  color: var(--accent-text);
  background: var(--accent-tint);
  border-radius: 50%;
  font-size: 1.375rem;
  font-weight: var(--weight-semibold);
}

.pc-summary__text {
  min-width: 0;
}

.pc-summary__name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.pc-summary__name h2 {
  margin: 0 var(--space-1) 0 0;
  font-size: var(--type-title2-size);
  line-height: 1.3;
}

.pc-summary__text p {
  display: flex;
  margin: var(--space-2) 0 0;
  gap: var(--space-1) var(--space-5);
  flex-wrap: wrap;
  color: var(--text-tertiary);
  font-size: var(--type-footnote-size);
}

.pc-summary__text p span {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.pc-summary__usage {
  display: grid;
  width: min(17rem, 40%);
  flex: 0 0 auto;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  color: var(--text-secondary);
  background: var(--bg-sunken);
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  text-align: left;
  transition: border-color var(--duration-fast, 120ms) ease;
}

.pc-summary__usage .pc-meter {
  background: var(--border-default);
}

.pc-summary__usage:hover {
  border-color: var(--border-default);
}

.pc-summary__usage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.pc-summary__usage small {
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
}

.pc-summary__usage b {
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.pc-badge {
  display: inline-flex;
  min-height: 1.5rem;
  padding: 0 var(--space-2);
  align-items: center;
  color: var(--text-secondary);
  background: var(--bg-sunken);
  border: 1px solid var(--border-hairline);
  border-radius: 999px;
  font-size: var(--type-caption-size);
  white-space: nowrap;
}

.pc-badge--accent {
  color: var(--accent-text);
  background: var(--accent-tint);
  border-color: transparent;
}

/* ---- Navigation + panel ---------------------------------------------- */
.pc-layout {
  margin-top: 0;
  grid-template-columns: 15rem minmax(0, 1fr);
  align-items: start;
}

.pc-nav {
  position: sticky;
  top: var(--space-5);
  display: grid;
  gap: var(--space-1);
}

.pc-nav button {
  display: grid;
  width: 100%;
  min-height: 3.5rem;
  padding: var(--space-2) var(--space-3);
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: center;
  gap: var(--space-3);
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  text-align: left;
}

.pc-nav button:hover {
  background: var(--bg-hover);
}

.pc-nav button.active {
  color: var(--accent-text);
  background: var(--bg-surface);
  border-color: var(--border-hairline);
  box-shadow: var(--shadow-1);
}

.pc-nav button > span {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.pc-nav strong {
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  font-weight: var(--weight-semibold);
}

.pc-nav button.active strong {
  color: var(--accent-text);
}

.pc-nav small {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-panel {
  min-height: 0;
  padding: var(--space-6);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-card);
}

.pc-panel__header {
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--border-hairline);
}

.pc-panel__header--row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.pc-panel__header h2 {
  margin: 0;
  font-size: var(--type-title3-size);
}

.pc-panel__header p {
  margin: var(--space-1) 0 0;
  color: var(--text-tertiary);
  font-size: var(--type-footnote-size);
}

/* ---- Settings rows ---------------------------------------------------- */
.pc-rows {
  margin: 0;
}

.pc-rows > div {
  display: grid;
  min-height: 4rem;
  padding: var(--space-4) 0;
  grid-template-columns: minmax(10rem, 16rem) minmax(0, 1fr);
  align-items: center;
  gap: var(--space-5);
  border-bottom: 1px solid var(--border-hairline);
}

.pc-rows > div:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.pc-rows dt {
  display: grid;
  gap: 2px;
}

.pc-rows dt strong {
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  font-weight: var(--weight-semibold);
}

.pc-rows dt small {
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
}

.pc-rows dd {
  margin: 0;
  min-width: 0;
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  overflow-wrap: anywhere;
}

.pc-rows code {
  padding: 0.15em 0.45em;
  color: var(--text-secondary);
  background: var(--bg-sunken);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: var(--type-footnote-size);
}

/* ---- Password -------------------------------------------------------- */
.pc-password {
  display: grid;
  margin-top: var(--space-5);
  grid-template-columns: minmax(0, 28rem) minmax(12rem, 16rem);
  align-items: start;
  gap: var(--space-6);
}

.pc-form {
  max-width: none;
}

.pc-note {
  padding: var(--space-4);
  color: var(--text-secondary);
  background: var(--bg-sunken);
  border-radius: var(--radius-control);
  font-size: var(--type-footnote-size);
}

.pc-note strong {
  color: var(--text-primary);
}

.pc-note ul {
  margin: var(--space-2) 0 0;
  padding-left: 1.1em;
  line-height: 1.8;
}

/* ---- Usage ----------------------------------------------------------- */
.pc-stats {
  display: grid;
  margin-top: var(--space-5);
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-control);
}

.pc-stats > div {
  display: grid;
  padding: var(--space-4);
  gap: var(--space-1);
  border-right: 1px solid var(--border-hairline);
}

.pc-stats > div:last-child {
  border-right: 0;
}

.pc-stats small {
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
}

.pc-stats b {
  color: var(--text-primary);
  font-size: var(--type-title2-size);
  font-variant-numeric: tabular-nums;
}

.pc-block {
  display: grid;
  margin-top: var(--space-6);
  gap: var(--space-3);
}

.pc-block__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.pc-block__head strong {
  font-size: var(--type-callout-size);
}

.pc-block__head small {
  color: var(--text-tertiary);
  font-size: var(--type-footnote-size);
}

.pc-meter,
.pc-split {
  display: block;
  height: 6px;
  overflow: hidden;
  background: var(--bg-sunken);
  border-radius: 999px;
}

.pc-meter--large,
.pc-split {
  height: 10px;
}

.pc-meter i,
.pc-split i {
  display: block;
  height: 100%;
  background: var(--accent-fill);
  border-radius: inherit;
  transition: width var(--duration-slow, 320ms) var(--ease-standard, ease);
}

.pc-meter--warning i {
  background: var(--warning-text);
}

.pc-meter--danger i {
  background: var(--danger-text);
}

.pc-split {
  background: var(--border-default);
}

.pc-legend {
  display: flex;
  margin: 0;
  gap: var(--space-6);
  flex-wrap: wrap;
}

.pc-legend > div {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.pc-legend dt {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-secondary);
  font-size: var(--type-footnote-size);
}

.pc-legend dd {
  margin: 0;
  color: var(--text-primary);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.pc-legend__dot {
  width: 8px;
  height: 8px;
  background: var(--border-default);
  border-radius: 50%;
}

.pc-legend__dot--platform {
  background: var(--accent-fill);
}

@media (max-width: 960px) {
  .pc-summary {
    align-items: stretch;
    flex-direction: column;
  }

  .pc-summary__usage {
    width: 100%;
  }

  .pc-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .pc-nav {
    position: static;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .pc-nav button {
    min-height: 2.75rem;
    grid-template-columns: 18px minmax(0, 1fr);
  }

  .pc-nav small {
    display: none;
  }

  .pc-password {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .pc-summary,
  .pc-panel {
    padding: var(--space-4);
  }

  .pc-nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pc-rows > div {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-2);
  }

  .pc-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pc-stats > div:nth-child(2) {
    border-right: 0;
  }

  .pc-stats > div:nth-child(-n + 2) {
    border-bottom: 1px solid var(--border-hairline);
  }
}
</style>
