<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { authState } from '@/auth/session'
import { agentPresentation } from '@/catalog/presentation'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import TaskComposer from '@/components/TaskComposer.vue'
import { applyTheme, getStoredTheme, type ThemeMode } from '@/utils/theme'

const route = useRoute()
const completedMessage = ref('')
const chatMode = ref(false)
const selectedPrompt = ref(typeof route.query.prompt === 'string' ? route.query.prompt : '')
const theme = ref<ThemeMode>(getStoredTheme())
const isDarkTheme = computed(() => theme.value === 'dark')
const greeting = computed(() => {
  const hour = new Date().getHours()
  const part = hour < 6 ? '夜深了' : hour < 11 ? '早上好' : hour < 13 ? '中午好' : hour < 18 ? '下午好' : '晚上好'
  const name = authState.user?.name?.trim()
  return name ? `${part}，${name}` : part
})

// Four frequent starting points, laid out as a symmetric row under the
// composer.  Labels are verbs; the glyph is the agent's own.
const shortcuts = [
  { code: 'literature_search', label: '查文献', to: '/agents/literature-search' },
  { code: 'paper_reading', label: '读论文', to: '/agents/paper-reading' },
  { code: 'manuscript_assistance', label: '写文稿', to: '/agents/manuscript-assistance' },
  { code: 'academic_translation', label: '做翻译', to: '/agents/academic-translation' },
].map((item) => ({ ...item, icon: agentPresentation[item.code].icon, hint: agentPresentation[item.code].tagline }))

function setChatMode(active: boolean) {
  chatMode.value = active
}

function toggleTheme() {
  theme.value = isDarkTheme.value ? 'light' : 'dark'
  applyTheme(theme.value)
}
</script>

<template>
  <div class="home-view" :class="{ 'home-view--chat-mode': chatMode }">
    <div class="home-topbar">
      <div class="home-actions">
        <a
          class="text-button"
          href="https://scnss29ndazj.feishu.cn/wiki/REdJwLqwziE7likYvO0cxC4onCb?from=from_copylink"
          target="_blank"
          rel="noopener noreferrer"
        >
          <ZyIcon name="help" :size="16" />使用指南
        </a>
        <span class="home-action-divider" aria-hidden="true"></span>
        <button
          class="icon-button"
          type="button"
          :aria-label="isDarkTheme ? '切换到浅色' : '切换到深色'"
          :title="isDarkTheme ? '切换到浅色' : '切换到深色'"
          @click="toggleTheme"
        >
          <Transition name="swap" mode="out-in">
            <ZyIcon v-if="isDarkTheme" key="moon" name="moon" :size="18" />
            <ZyIcon v-else key="sun" name="sun" :size="18" />
          </Transition>
        </button>
      </div>
    </div>

    <section class="home-hero">
      <div class="home-hero__intro">
        <h1>{{ greeting }}</h1>
        <p class="home-subtitle">今天想研究什么？</p>
      </div>

      <TaskComposer
        :preset="selectedPrompt"
        @chat-mode-change="setChatMode"
        @completed="completedMessage = $event"
      />
      <p v-if="completedMessage" class="completion-note">{{ completedMessage }}</p>

      <nav v-if="!chatMode" class="home-shortcuts stagger" aria-label="常用入口">
        <RouterLink v-for="item in shortcuts" :key="item.code" class="home-shortcut lift draw-on-hover" :to="item.to">
          <span class="home-shortcut__icon"><ZyIcon :name="item.icon" :size="20" /></span>
          <strong>{{ item.label }}</strong>
          <small>{{ item.hint }}</small>
        </RouterLink>
      </nav>
    </section>
  </div>
</template>
