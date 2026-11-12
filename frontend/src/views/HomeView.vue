<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { CircleHelp, Moon, Sun } from 'lucide-vue-next'

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
  if (hour < 11) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

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
          <CircleHelp :size="15" />使用指南
        </a>
        <span class="home-action-divider" aria-hidden="true"></span>
        <button
          class="icon-button"
          type="button"
          :aria-label="isDarkTheme ? '切换到白天模式' : '切换到黑夜模式'"
          :title="isDarkTheme ? '切换到白天模式' : '切换到黑夜模式'"
          @click="toggleTheme"
        >
          <Moon v-if="isDarkTheme" :size="18" />
          <Sun v-else :size="18" />
        </button>
      </div>
    </div>

    <section class="home-hero">
      <div class="home-hero__intro">
        <p class="eyebrow">ZHIYAN RESEARCH WORKSPACE</p>
        <h1>
          <span>{{ greeting }}，</span>
          <span>从问题出发建立你的文献脉络。</span>
        </h1>
        <div class="home-title-rule" aria-hidden="true"><span></span></div>
        <p class="home-subtitle">检索、精读、分析和写作由同一科研工作流持续承接。</p>
      </div>

      <TaskComposer
        :preset="selectedPrompt"
        @chat-mode-change="setChatMode"
        @completed="completedMessage = $event"
      />
      <p v-if="completedMessage" class="completion-note">{{ completedMessage }}</p>
    </section>

    <footer class="home-folio" aria-label="页面信息">
      <span>FOLIO / 001</span>
      <span>EDITORIAL RESEARCH ATELIER</span>
    </footer>
  </div>
</template>
