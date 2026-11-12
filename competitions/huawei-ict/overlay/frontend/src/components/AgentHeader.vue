<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { agentPresentation, toolPresentation } from '@/catalog/presentation'
import ZyIcon from '@/components/icons/ZyIcon.vue'

// The bar every agent and tool page opens with: where you are, what this
// page is for, and the way back to the list it came from.
const props = withDefaults(defineProps<{
  code: string
  title: string
  kind?: 'agent' | 'tool'
}>(), { kind: 'agent' })

const entry = computed(() => (props.kind === 'tool' ? toolPresentation[props.code] : agentPresentation[props.code]))
const back = computed(() => (props.kind === 'tool'
  ? { to: '/tools', label: '返回工具集' }
  : { to: '/agents', label: '返回智能体' }))
</script>

<template>
  <header class="agent-bar">
    <div class="agent-bar__identity">
      <RouterLink class="agent-bar__back" :to="back.to" :aria-label="back.label" :title="back.label">
        <ZyIcon name="chevronRight" :size="16" />
      </RouterLink>
      <span class="agent-bar__mark"><ZyIcon :name="entry?.icon ?? (kind === 'tool' ? 'tools' : 'agents')" :size="18" /></span>
      <span class="agent-bar__text">
        <strong>{{ title }}</strong>
        <small v-if="entry">{{ entry.tagline }}</small>
      </span>
    </div>
    <div class="agent-bar__actions">
      <slot />
    </div>
  </header>
</template>
