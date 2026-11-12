<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getData } from '@/api/http'
import {
  agentPresentation,
  categoryLabel,
  skillIcon,
  statusLabel,
  toolPresentation,
} from '@/catalog/presentation'
import EmptyNotice from '@/components/EmptyNotice.vue'
import type { GlyphName } from '@/components/icons/glyphs'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import PageHeader from '@/components/PageHeader.vue'
import type { CatalogItem } from '@/types'

const props = defineProps<{
  kind: 'agents' | 'agent-teams' | 'tools' | 'skills'
  title: string
}>()

const ALL = '全部'

const router = useRouter()
const items = ref<CatalogItem[]>([])
const loading = ref(true)
const loadFailed = ref(false)
const query = ref('')
const activeCategory = ref(ALL)

const pageDescriptions = {
  agents: '每个智能体负责一类科研任务，选一个开始。',
  'agent-teams': '把几个智能体串起来，按顺序完成一件更长的任务。',
  tools: '处理文档、公式、表格和引用格式的常用工具。',
  skills: '可以交给智能体调用的方法和流程说明。',
}
const pageDescription = computed(() => pageDescriptions[props.kind])

interface Card {
  item: CatalogItem
  icon: GlyphName
  category: string
  summary: string
  state: string
}

// Agents and tools take their glyph and wording from the presentation
// registry; skills are uploaded content and keep their own description.
const cards = computed<Card[]>(() => items.value.map((item) => {
  const code = item.code ?? ''
  const entry = props.kind === 'agents' ? agentPresentation[code] : props.kind === 'tools' ? toolPresentation[code] : undefined
  const icon: GlyphName = entry?.icon
    ?? (props.kind === 'skills' ? skillIcon(item) : props.kind === 'agent-teams' ? 'teams' : props.kind === 'tools' ? 'tools' : 'agents')
  return {
    item,
    icon,
    category: categoryLabel(item.category),
    summary: entry?.summary ?? item.description,
    state: props.kind === 'skills' ? statusLabel(item.status) : item.status || '可用',
  }
}))

const categories = computed(() => [ALL, ...new Set(cards.value.map((card) => card.category).filter(Boolean))])
const filteredCards = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return cards.value.filter((card) => {
    const categoryMatches = activeCategory.value === ALL || card.category === activeCategory.value
    const text = `${card.item.name}${card.summary}${card.category}${(card.item.tags ?? []).join('')}`.toLowerCase()
    return categoryMatches && (!keyword || text.includes(keyword))
  })
})

const emptyMessage = computed(() => {
  if (loadFailed.value) return '列表没有加载出来，请刷新页面再试。'
  if (query.value.trim() || activeCategory.value !== ALL) return '没有符合条件的内容，换个关键词或分类试试。'
  return '这里还没有内容。'
})

async function loadItems() {
  loading.value = true
  loadFailed.value = false
  activeCategory.value = ALL
  query.value = ''
  try {
    items.value = await getData<CatalogItem[]>(`/${props.kind}`)
  } catch {
    items.value = []
    loadFailed.value = true
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
    <PageHeader :title="title" :description="pageDescription">
      <label class="search-input">
        <ZyIcon name="search" :size="16" />
        <input v-model="query" type="search" :placeholder="`在${title}里搜索`" />
      </label>
    </PageHeader>

    <!-- A wrapping chip row, not a segmented control: the category list
         comes from the data and routinely runs past ten entries. -->
    <div v-if="categories.length > 2" class="filter-chips" role="tablist" aria-label="分类筛选">
      <button
        v-for="category in categories"
        :key="category"
        type="button"
        role="tab"
        :aria-selected="activeCategory === category"
        :class="['filter-chip', { 'filter-chip--active': activeCategory === category }]"
        @click="activeCategory = category"
      >{{ category }}</button>
    </div>

    <div v-if="loading" class="catalog-grid" aria-label="正在加载">
      <div v-for="index in 6" :key="index" class="catalog-card catalog-card--placeholder skeleton"></div>
    </div>
    <EmptyNotice v-else-if="filteredCards.length === 0" :message="emptyMessage" />
    <div v-else :key="activeCategory" class="catalog-grid stagger">
      <article
        v-for="card in filteredCards"
        :key="card.item.id"
        class="catalog-card lift draw-on-hover"
        :class="{ 'catalog-card--unavailable': card.item.readiness === 'UNAVAILABLE' }"
        role="link"
        :tabindex="card.item.readiness === 'UNAVAILABLE' ? -1 : 0"
        :aria-disabled="card.item.readiness === 'UNAVAILABLE'"
        @click="useItem(card.item)"
        @keydown.enter="useItem(card.item)"
      >
        <div class="catalog-card__header">
          <span class="catalog-icon"><ZyIcon :name="card.icon" :size="22" /></span>
          <span v-if="card.category" class="catalog-card__category">{{ card.category }}</span>
        </div>
        <h2>{{ card.item.name }}</h2>
        <p>{{ card.summary }}</p>
        <div v-if="card.item.tags?.length" class="member-list">
          <span v-for="tag in card.item.tags.slice(0, 4)" :key="tag">{{ tag }}</span>
        </div>
        <footer>
          <span :class="['availability', card.item.readiness?.toLowerCase()]" :title="card.item.readiness_detail"><i></i>{{ card.state }}</span>
          <span class="catalog-card__open nudge-arrow">{{ kind === 'skills' ? '查看' : '打开' }}<ZyIcon name="arrowRight" :size="15" /></span>
        </footer>
      </article>
    </div>
  </div>
</template>
