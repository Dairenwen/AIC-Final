<script setup lang="ts">
import { computed } from 'vue'

import { agentPresentation } from '@/catalog/presentation'
import ZyIcon from '@/components/icons/ZyIcon.vue'

// The opening of an empty agent workspace: its glyph, drawn once as the
// page arrives, the question it starts from, and what will happen next.
const props = defineProps<{ code: string }>()
const entry = computed(() => agentPresentation[props.code])
</script>

<template>
  <div v-if="entry" class="agent-intro">
    <span class="agent-intro__icon"><ZyIcon class="zy-icon--draw" :name="entry.icon" :size="30" /></span>
    <h1>{{ entry.heading }}</h1>
    <p>{{ entry.lead }}</p>
  </div>
</template>
