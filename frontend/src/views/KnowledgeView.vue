<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const allowedSections = new Set(['search', 'collections', 'upload', 'agent'])
const frameSource = computed(() => {
  const section = String(route.params.section || 'search')
  const tab = allowedSections.has(section) ? section : 'search'
  return `/api/v1/knowledge-base/ui?embed=1&mode=user&tab=${encodeURIComponent(tab)}`
})
</script>

<template>
  <div class="knowledge-platform-page">
    <iframe
      class="knowledge-platform-frame"
      :key="frameSource"
      :src="frameSource"
      title="智研个人知识库"
      referrerpolicy="same-origin"
    ></iframe>
  </div>
</template>

<style scoped>
.knowledge-platform-page {
  height: 100vh;
  min-height: 620px;
  background: #eef1ef;
}

.knowledge-platform-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: #eef1ef;
}

@media (max-width: 760px) {
  .knowledge-platform-page {
    height: calc(100vh - 54px);
    min-height: 560px;
  }
}
</style>
