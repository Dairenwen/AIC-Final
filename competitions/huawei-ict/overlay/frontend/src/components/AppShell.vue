<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { getData } from '@/api/http'
import { authState, logout } from '@/auth/session'
import BrandMark from '@/components/icons/BrandMark.vue'
import type { GlyphName } from '@/components/icons/glyphs'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import { taskHistoryLocation } from '@/router/taskHistory'
import type { HistoryItem, Profile } from '@/types'

interface NavItem {
  label: string
  to: string
  icon: GlyphName
}

const HISTORY_COLLAPSED_KEY = 'zhiyan.sidebar.historyCollapsed'

const route = useRoute()
const router = useRouter()
const sidebarOpen = ref(false)
const knowledgeAdminOpen = ref(true)
const historyCollapsed = ref(readFlag(HISTORY_COLLAPSED_KEY))
const profile = ref<Profile>({
  id: '',
  name: authState.user?.name ?? '',
  organization: authState.user?.organization ?? '',
  role: authState.user?.role ?? 'normal_user',
  plan: '',
  modelConfigured: true,
})
const history = ref<HistoryItem[]>([])

const navItems: NavItem[] = [
  { label: '首页', to: '/', icon: 'home' },
  { label: '科研项目', to: '/projects', icon: 'projects' },
  { label: '我的智能体', to: '/agents', icon: 'agents' },
  { label: '我的智囊团', to: '/teams', icon: 'teams' },
  { label: '科研工具集', to: '/tools', icon: 'tools' },
  { label: '科研技能库', to: '/skills', icon: 'skills' },
]

const academicSpaceItems: NavItem[] = [
  { label: '我的知识库', to: '/academic-space/knowledge', icon: 'knowledge' },
]

const knowledgeAdminItems: NavItem[] = [
  { label: '知识库管理', to: '/admin/knowledge-base/knowledge', icon: 'database' },
  { label: '训练集生成', to: '/admin/knowledge-base/training-set', icon: 'training' },
]

function isActive(item: NavItem) {
  return route.path === item.to || (item.to !== '/' && route.path.startsWith(`${item.to}/`))
}

/*
 * One highlight slides between navigation entries.  It is measured from the
 * entry that is currently marked active, so it stays correct when a group is
 * collapsed, the list scrolls, or the window is resized.
 */
const navArea = ref<HTMLElement | null>(null)
const indicator = ref({ y: 0, height: 0, ready: false, instant: true })
let navObserver: ResizeObserver | null = null

function placeIndicator() {
  const area = navArea.value
  const active = area?.querySelector<HTMLElement>('[data-nav-active="true"]')
  if (!area || !active) {
    indicator.value = { ...indicator.value, ready: false }
    return
  }
  const areaTop = area.getBoundingClientRect().top
  const box = active.getBoundingClientRect()
  const wasReady = indicator.value.ready
  indicator.value = { y: box.top - areaTop, height: box.height, ready: true, instant: !wasReady }
  if (!wasReady) requestAnimationFrame(() => { indicator.value = { ...indicator.value, instant: false } })
}

watch(() => [route.path, knowledgeAdminOpen.value, authState.user?.role], () => nextTick(placeIndicator))

function toggleHistory() {
  historyCollapsed.value = !historyCollapsed.value
  writeFlag(HISTORY_COLLAPSED_KEY, historyCollapsed.value)
}

function readFlag(key: string) {
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, value: boolean) {
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    // Storage can be unavailable; the preference then lasts for this visit.
  }
}

function closeSidebar() {
  sidebarOpen.value = false
}

async function signOut() {
  await logout()
  closeSidebar()
  await router.replace({ name: 'login' })
}

onMounted(async () => {
  nextTick(placeIndicator)
  if (navArea.value && typeof ResizeObserver !== 'undefined') {
    navObserver = new ResizeObserver(placeIndicator)
    navObserver.observe(navArea.value)
  }
  try {
    ;[profile.value, history.value] = await Promise.all([
      getData<Profile>('/users/me'),
      getData<HistoryItem[]>('/history'),
    ])
  } catch {
    // Keep the navigation frame available while the database API is starting.
  }
})

onBeforeUnmount(() => navObserver?.disconnect())
</script>

<template>
  <div class="app-shell">
    <header class="mobile-header">
      <button class="icon-button" type="button" aria-label="打开导航" title="打开导航" @click="sidebarOpen = true">
        <ZyIcon name="menu" :size="20" />
      </button>
      <RouterLink class="mobile-brand" to="/">
        <BrandMark :size="26" />
        <strong>智研</strong>
      </RouterLink>
      <RouterLink class="icon-button" to="/profile" aria-label="个人中心" title="个人中心">
        <ZyIcon name="user" :size="19" />
      </RouterLink>
    </header>

    <aside class="sidebar" :class="{ 'sidebar--open': sidebarOpen }">
      <div class="sidebar__header">
        <RouterLink class="brand" to="/" @click="closeSidebar">
          <BrandMark :size="30" />
          <span>
            <strong>智研</strong>
            <small>科研工作台</small>
          </span>
        </RouterLink>
        <button class="sidebar-close" type="button" aria-label="关闭导航" @click="closeSidebar">
          <ZyIcon name="close" :size="18" />
        </button>
      </div>

      <RouterLink class="new-task-button" to="/" @click="closeSidebar">
        <ZyIcon name="plus" :size="16" />
        <span>新建任务</span>
      </RouterLink>

      <div ref="navArea" class="sidebar__nav">
        <span
          class="track-indicator"
          :class="{ 'track-indicator--ready': indicator.ready, 'track-indicator--instant': indicator.instant }"
          :style="{ '--track-y': `${indicator.y}px`, height: `${indicator.height}px`, width: '100%' }"
          aria-hidden="true"
        ></span>

        <nav class="primary-nav" aria-label="主导航">
          <RouterLink
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            :class="{ 'nav-item--active': isActive(item) }"
            :data-nav-active="isActive(item)"
            @click="closeSidebar"
          >
            <ZyIcon :name="item.icon" :size="18" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </nav>

        <section class="academic-space-nav">
          <div class="academic-space-nav__title">学术空间</div>
          <nav aria-label="学术空间">
            <RouterLink
              v-for="item in academicSpaceItems"
              :key="item.to"
              :to="item.to"
              class="nav-item"
              :class="{ 'nav-item--active': route.path === item.to }"
              :data-nav-active="route.path === item.to"
              @click="closeSidebar"
            >
              <ZyIcon :name="item.icon" :size="18" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </nav>
        </section>

        <section v-if="authState.user?.role === 'system_admin'" class="knowledge-admin-nav">
          <button
            class="knowledge-admin-toggle"
            type="button"
            :aria-expanded="knowledgeAdminOpen"
            @click="knowledgeAdminOpen = !knowledgeAdminOpen"
          >
            <span>平台数据</span>
            <ZyIcon class="disclosure" :class="{ 'disclosure--open': knowledgeAdminOpen }" name="chevronRight" :size="14" />
          </button>
          <nav v-if="knowledgeAdminOpen" class="knowledge-admin-items" aria-label="平台数据">
            <RouterLink
              v-for="item in knowledgeAdminItems"
              :key="item.to"
              :to="item.to"
              class="nav-item"
              :class="{ 'nav-item--active': route.path === item.to }"
              :data-nav-active="route.path === item.to"
              @click="closeSidebar"
            >
              <ZyIcon :name="item.icon" :size="18" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </nav>
        </section>
      </div>

      <section class="history-section" :class="{ 'history-section--collapsed': historyCollapsed }">
        <button
          class="sidebar-section-title"
          type="button"
          :aria-expanded="!historyCollapsed"
          @click="toggleHistory"
        >
          <span>最近的任务</span>
          <ZyIcon class="disclosure" :class="{ 'disclosure--open': !historyCollapsed }" name="chevronRight" :size="14" />
        </button>
        <div v-if="!historyCollapsed" class="history-list">
          <RouterLink
            v-for="item in history"
            :key="item.id"
            :to="taskHistoryLocation(item)"
            class="history-item"
            :class="{ 'history-item--active': route.query.task === item.id }"
            :title="item.title"
            @click="closeSidebar"
          >
            <span>{{ item.title }}</span>
            <time>{{ item.time }}</time>
          </RouterLink>
          <p v-if="history.length === 0" class="history-empty">做过的任务会出现在这里</p>
        </div>
      </section>

      <div class="sidebar__footer">
        <RouterLink
          v-if="profile.role === 'system_admin'"
          class="admin-link"
          :class="{ 'nav-item--active': route.path.startsWith('/admin/system') }"
          to="/admin"
          @click="closeSidebar"
        >
          <ZyIcon name="settings" :size="18" />
          <span>系统配置</span>
        </RouterLink>
        <div class="sidebar-account">
          <RouterLink class="user-summary" to="/profile" @click="closeSidebar">
            <span class="avatar">{{ (profile.name || '研').slice(0, 1) }}</span>
            <span class="user-summary__text">
              <strong>{{ profile.name || '未命名' }}</strong>
              <small>{{ profile.organization || '个人中心' }}</small>
            </span>
          </RouterLink>
          <button class="sidebar-logout" type="button" aria-label="退出登录" title="退出登录" @click="signOut">
            <ZyIcon name="logout" :size="17" />
          </button>
        </div>
      </div>
    </aside>

    <Transition name="pop">
      <button v-if="sidebarOpen" class="sidebar-overlay" type="button" aria-label="关闭导航" @click="closeSidebar"></button>
    </Transition>

    <main class="main-content">
      <!-- Keyed by route name: pages that share a component (the three
           catalogues, the four converters) still get their own arrival. -->
      <RouterView v-slot="{ Component, route: current }">
        <Transition name="page" mode="out-in">
          <component :is="Component" :key="String(current.name)" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>
