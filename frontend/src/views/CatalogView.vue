<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUpRight, Bot, Download, Search, SlidersHorizontal, UsersRound, Wrench } from 'lucide-vue-next'

import { getData } from '@/api/http'
import EmptyNotice from '@/components/EmptyNotice.vue'
import PageHeader from '@/components/PageHeader.vue'
import type { CatalogItem } from '@/types'

const props = defineProps<{
  kind: 'agents' | 'agent-teams' | 'tools' | 'skills'
  title: string
  eyebrow: string
}>()

const router = useRouter()
const items = ref<CatalogItem[]>([])
const loading = ref(true)
const query = ref('')
const activeCategory = ref('全部')

const pageDescriptions = {
  agents: '选择合适能力，直接进入科研任务。',
  'agent-teams': '组合多个专业智能体，完成更长链路的科研任务。',
  tools: '调用实用科研工具，高效完成文档、数据与写作处理。',
  skills: '沉淀可复用的科研方法与工作流，让专业能力随需调用。',
}
const pageDescription = computed(() => pageDescriptions[props.kind])

const categories = computed(() => [
  '全部',
  ...new Set(items.value.map((item) => item.category).filter((value): value is string => Boolean(value))),
])
const filteredItems = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return items.value.filter((item) => {
    const categoryMatches = activeCategory.value === '全部' || item.category === activeCategory.value
    const textMatches = !keyword || `${item.name}${item.description}${item.category ?? ''}`.toLowerCase().includes(keyword)
    return categoryMatches && textMatches
  })
})

async function loadItems() {
  loading.value = true
  activeCategory.value = '全部'
  try {
    items.value = await getData<CatalogItem[]>(`/${props.kind}`)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function useItem(item: CatalogItem) {
  if (item.readiness === 'UNAVAILABLE') return
  if (item.route) {
    router.push(item.route)
    return
  }
  router.push({ path: '/', query: { prompt: `使用${item.name}：` } })
}

watch(() => props.kind, loadItems, { immediate: true })
</script>

<template>
  <div class="workspace-page catalog-page">
    <PageHeader :eyebrow="eyebrow" :title="title" :description="pageDescription">
      <button class="secondary-button" type="button"><SlidersHorizontal :size="15" />管理{{ kind === 'skills' ? '我的技能' : '常用项' }}</button>
    </PageHeader>

    <div class="catalog-toolbar">
      <div class="segment-control" role="tablist" aria-label="分类筛选">
        <button
          v-for="category in categories"
          :key="category"
          type="button"
          :class="{ active: activeCategory === category }"
          @click="activeCategory = category"
        >{{ category }}</button>
      </div>
      <label class="search-input">
        <Search :size="16" />
        <input v-model="query" type="search" placeholder="搜索名称或能力" />
      </label>
    </div>

    <div v-if="loading" class="catalog-grid" aria-label="正在加载">
      <div v-for="index in 6" :key="index" class="catalog-card skeleton"></div>
    </div>
    <EmptyNotice v-else-if="filteredItems.length === 0" message="未找到匹配内容，请检查 Flask 服务或调整筛选条件。" />
    <div v-else class="catalog-grid">
      <article v-for="item in filteredItems" :key="item.id" class="catalog-card">
        <div class="catalog-card__header">
          <span class="catalog-icon"><UsersRound v-if="kind === 'agent-teams'" :size="18" /><Wrench v-else-if="kind === 'tools'" :size="18" /><Bot v-else :size="18" /></span>
          <span v-if="item.category || item.status" class="status-tag">{{ item.category || item.status }}</span>
        </div>
        <h2>{{ item.name }}</h2>
        <p>{{ item.description }}</p>
        <div v-if="item.members" class="member-list">
          <span v-for="member in item.members" :key="member">{{ member }}</span>
        </div>
        <div v-if="item.tags" class="member-list">
          <span v-for="tag in item.tags" :key="tag">{{ tag }}</span>
        </div>
        <footer>
          <span v-if="item.downloads" class="download-count"><Download :size="13" />{{ item.downloads }}</span>
          <span v-else :class="['availability', item.readiness?.toLowerCase()]" :title="item.readiness_detail"><i></i>{{ item.status || '可用' }}</span>
          <button type="button" :disabled="item.readiness === 'UNAVAILABLE'" @click="useItem(item)">使用<ArrowUpRight :size="14" /></button>
        </footer>
      </article>
    </div>
  </div>
</template>
