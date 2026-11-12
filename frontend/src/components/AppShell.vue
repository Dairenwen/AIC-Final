<script setup lang="ts">
import type { Component } from 'vue'
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Bell,
  BookOpen,
  BookMarked,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Clock3,
  Database,
  FileCheck2,
  FolderKanban,
  Home,
  LogOut,
  Menu,
  PanelLeftClose,
  Plus,
  Settings,
  Sparkles,
  UserRound,
  Wrench,
  X,
} from 'lucide-vue-next'

import { getData } from '@/api/http'
import { authState, logout } from '@/auth/session'
import { taskHistoryLocation } from '@/router/taskHistory'
import type { HistoryItem, Profile } from '@/types'

interface NavItem {
  label: string
  to: string
  icon: Component
}

const route = useRoute()
const router = useRouter()
const sidebarOpen = ref(false)
const knowledgeAdminOpen = ref(true)
const profile = ref<Profile>({
  id: '',
  name: '未登录',
  organization: '未设置机构',
  role: 'normal_user',
  plan: '科研基础版',
  modelConfigured: true,
})
const history = ref<HistoryItem[]>([])

const navItems: NavItem[] = [
  { label: '首页', to: '/', icon: Home },
  { label: '科研项目', to: '/projects', icon: FolderKanban },
  { label: '我的智能体', to: '/agents', icon: Bot },
  { label: '我的智囊团', to: '/teams', icon: BrainCircuit },
  { label: '科研工具集', to: '/tools', icon: Wrench },
  { label: '科研技能库', to: '/skills', icon: Sparkles },
]

const academicSpaceItems: NavItem[] = [
  { label: '我的知识库', to: '/academic-space/knowledge', icon: BookMarked },
]

const knowledgeAdminItems: NavItem[] = [
  { label: '知识库管理', to: '/admin/knowledge-base/knowledge', icon: Database },
  { label: '训练集生成', to: '/admin/knowledge-base/training-set', icon: FileCheck2 },
]

function closeSidebar() {
  sidebarOpen.value = false
}

async function signOut() {
  await logout()
  closeSidebar()
  await router.replace({ name: 'login' })
}

onMounted(async () => {
  try {
    ;[profile.value, history.value] = await Promise.all([
      getData<Profile>('/users/me'),
      getData<HistoryItem[]>('/history'),
    ])
  } catch {
    // Keep the navigation frame available while the database API is starting.
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="mobile-header">
      <button class="icon-button" type="button" aria-label="打开导航" title="打开导航" @click="sidebarOpen = true">
        <Menu :size="19" />
      </button>
      <RouterLink class="mobile-brand" to="/">
        <span class="brand-mark">
          <BookOpen :size="17" color="url(#zhiyan-mobile-logo-gradient)">
            <defs>
              <linearGradient id="zhiyan-mobile-logo-gradient" gradientUnits="userSpaceOnUse" x1="2" y1="2" x2="22" y2="22">
                <stop offset="0" stop-color="#1E3A8A" />
                <stop offset="1" stop-color="#3B82F6" />
              </linearGradient>
            </defs>
          </BookOpen>
        </span>
        <strong>智研</strong>
      </RouterLink>
      <button class="icon-button" type="button" aria-label="通知" title="通知">
        <Bell :size="18" />
      </button>
    </header>

    <aside class="sidebar" :class="{ 'sidebar--open': sidebarOpen }">
      <div class="sidebar__header">
        <RouterLink class="brand" to="/" @click="closeSidebar">
          <span class="brand-mark">
            <BookOpen :size="18" color="url(#zhiyan-sidebar-logo-gradient)">
              <defs>
                <linearGradient id="zhiyan-sidebar-logo-gradient" gradientUnits="userSpaceOnUse" x1="2" y1="2" x2="22" y2="22">
                  <stop offset="0" stop-color="#1E3A8A" />
                  <stop offset="1" stop-color="#3B82F6" />
                </linearGradient>
              </defs>
            </BookOpen>
          </span>
          <span>
            <strong>智研</strong>
            <small>ZHIYAN</small>
          </span>
        </RouterLink>
        <button class="sidebar-close" type="button" aria-label="关闭导航" @click="closeSidebar">
          <X :size="18" />
        </button>
      </div>

      <RouterLink class="new-task-button" to="/" @click="closeSidebar">
        <Plus :size="17" />
        <span>新建任务</span>
      </RouterLink>

      <nav class="primary-nav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item--active': route.path === item.to || (item.to !== '/' && route.path.startsWith(`${item.to}/`)) }"
          @click="closeSidebar"
        >
          <component :is="item.icon" :size="17" />
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
            @click="closeSidebar"
          >
            <component :is="item.icon" :size="17" />
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
          <span><Database :size="14" />知识库管理平台</span>
          <ChevronDown v-if="knowledgeAdminOpen" :size="14" />
          <ChevronRight v-else :size="14" />
        </button>
        <nav v-if="knowledgeAdminOpen" class="knowledge-admin-items" aria-label="知识库管理平台">
          <RouterLink
            v-for="item in knowledgeAdminItems"
            :key="item.to"
            :to="item.to"
            class="knowledge-admin-item"
            :class="{ 'knowledge-admin-item--active': route.path === item.to }"
            @click="closeSidebar"
          >
            <component :is="item.icon" :size="15" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </nav>
      </section>

      <section class="history-section">
        <div class="sidebar-section-title">
          <span><Clock3 :size="14" />历史记录</span>
          <button type="button" aria-label="收起历史" title="收起历史"><PanelLeftClose :size="14" /></button>
        </div>
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
      </section>

      <div class="sidebar__footer">
        <RouterLink
          v-if="profile.role === 'system_admin'"
          class="admin-link"
          :class="{ 'nav-item--active': route.path.startsWith('/admin/system') }"
          to="/admin"
          @click="closeSidebar"
        >
          <Settings :size="16" />
          <span>系统配置</span>
        </RouterLink>
        <div class="sidebar-account">
          <RouterLink class="user-summary" to="/profile" @click="closeSidebar">
            <span class="avatar"><UserRound :size="18" /></span>
            <span class="user-summary__text">
              <strong>{{ profile.name }}</strong>
              <small>{{ profile.organization }}</small>
            </span>
            <span class="user-status" title="服务正常"></span>
          </RouterLink>
          <button class="sidebar-logout" type="button" aria-label="退出登录" title="退出登录" @click="signOut">
            <LogOut :size="16" />
          </button>
        </div>
      </div>
    </aside>

    <button v-if="sidebarOpen" class="sidebar-overlay" type="button" aria-label="关闭导航" @click="closeSidebar"></button>

    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>
