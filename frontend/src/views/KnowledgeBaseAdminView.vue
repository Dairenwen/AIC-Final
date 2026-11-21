<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const tabMap: Record<string, string> = {
  dashboard: 'dashboard',
  knowledge: 'knowledge',
  'training-set': 'qaGenerate',
  exceptions: 'exceptions',
  audit: 'audit',
  permissions: 'permissions',
}

const frameSource = computed(() => {
  const section = String(route.params.section || 'dashboard')
  const tab = tabMap[section] || 'dashboard'
  return `/api/v1/knowledge-base/ui?embed=1&tab=${encodeURIComponent(tab)}`
})
</script>

<template>
  <div class="knowledge-base-admin-page">
    <iframe
      :key="frameSource"
      class="knowledge-base-frame"
      :src="frameSource"
      title="智研知识库管理平台"
      referrerpolicy="same-origin"
    ></iframe>
  </div>
</template>

<style scoped>
/* The knowledge-base admin platform is a separate front-end served by
 * backend/knowledge_base_runtime and embedded here in an iframe, so the
 * design tokens do not reach inside it.  This wrapper only provides the
 * frame; the decorative gradient it used to paint behind the iframe was
 * never visible and used colours outside the palette. */
.knowledge-base-admin-page {
  height: var(--viewport-height);
  min-height: 38rem;
  overflow: hidden;
  background: var(--bg-surface);
}

.knowledge-base-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: var(--bg-surface);
}

@media (max-width: 1024px) {
  .knowledge-base-admin-page {
    height: calc(var(--viewport-height) - 3.25rem);
    min-height: 35rem;
  }
}
</style>
