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
.knowledge-base-admin-page {
  height: 100vh;
  min-height: 620px;
  overflow: hidden;
  background:
    radial-gradient(circle at 7% 7%, rgb(84 185 255 / 8%), transparent 26%),
    linear-gradient(135deg, #ffffff 0%, #f8fbfe 50%, #f7f8fa 100%);
}

.knowledge-base-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: #ffffff;
}

@media (max-width: 760px) {
  .knowledge-base-admin-page {
    height: calc(100vh - 54px);
    min-height: 560px;
  }
}
</style>
