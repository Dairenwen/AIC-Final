<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Check, Copy, ExternalLink, FileText } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import { getData } from '@/api/http'
import EmptyNotice from '@/components/EmptyNotice.vue'
import PageHeader from '@/components/PageHeader.vue'
import type { SkillDetail } from '@/types'

const route = useRoute()
const router = useRouter()
const detail = ref<SkillDetail | null>(null)
const loading = ref(true)
const loadError = ref(false)
const selectedFile = ref('')
const copied = ref(false)

const activeContent = computed(() => {
  if (!detail.value || !selectedFile.value) return detail.value?.fullContent || ''
  return detail.value.files.find((file) => file.path === selectedFile.value)?.content || ''
})

async function loadSkill() {
  loading.value = true
  loadError.value = false
  try {
    detail.value = await getData<SkillDetail>(`/skills/${String(route.params.id)}`)
    selectedFile.value = ''
  } catch {
    loadError.value = true
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function copyContent() {
  if (!activeContent.value) return
  await navigator.clipboard.writeText(activeContent.value)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/skills')
}

onMounted(loadSkill)
</script>

<template>
  <div v-if="loading" class="workspace-page skill-detail-page">
    <div class="table-loading">正在读取技能内容...</div>
  </div>
  <div v-else-if="loadError || !detail" class="workspace-page skill-detail-page">
    <EmptyNotice message="技能内容暂时无法读取，请返回科研技能库重试。" />
  </div>
  <div v-else class="workspace-page skill-detail-page">
    <PageHeader eyebrow="RESEARCH SKILL" :title="detail.name" :description="detail.description">
      <button class="icon-button" type="button" aria-label="返回科研技能库" title="返回科研技能库" @click="goBack">
        <ArrowLeft :size="17" />
      </button>
    </PageHeader>

    <div class="skill-detail-meta">
      <span class="status-tag">{{ detail.category || '科研技能' }}</span>
      <span v-for="tag in detail.tags" :key="tag" class="member-list__tag">{{ tag }}</span>
      <span class="skill-detail-meta__source">
        {{ detail.author || '开放技能库' }} · {{ detail.downloadStatus === 'DOWNLOADED' ? `已下载 ${detail.fileCount} 个文件` : '仅保存爬取描述' }}
      </span>
      <a v-if="detail.sourceUrl" class="text-link" :href="detail.sourceUrl" target="_blank" rel="noreferrer">
        来源链接 <ExternalLink :size="13" />
      </a>
    </div>

    <section class="skill-content-layout">
      <aside v-if="detail.files.length" class="skill-file-panel" aria-label="技能文件">
        <div class="skill-file-panel__heading"><FileText :size="15" />文件目录</div>
        <button
          type="button"
          class="skill-file-item"
          :class="{ active: !selectedFile }"
          @click="selectedFile = ''"
        >完整内容
        </button>
        <button
          v-for="file in detail.files"
          :key="file.path"
          type="button"
          class="skill-file-item"
          :class="{ active: selectedFile === file.path }"
          @click="selectedFile = file.path"
        >{{ file.path }}
        </button>
      </aside>

      <article class="skill-content-panel">
        <header class="skill-content-panel__header">
          <div>
            <span class="eyebrow">{{ selectedFile || 'FULL CONTENT' }}</span>
            <strong>{{ detail.downloadStatus === 'DOWNLOADED' ? '技能库完整内容' : '爬取到的技能描述' }}</strong>
          </div>
          <button class="secondary-button" type="button" :disabled="!activeContent" @click="copyContent">
            <Check v-if="copied" :size="15" />
            <Copy v-else :size="15" />
            {{ copied ? '已复制' : '复制内容' }}
          </button>
        </header>
        <pre class="skill-content">{{ activeContent || '暂无可展示内容' }}</pre>
        <p v-if="detail.downloadError" class="skill-content-warning">下载失败：{{ detail.downloadError }}</p>
      </article>
    </section>
  </div>
</template>

<style scoped>
.skill-detail-meta {
  display: flex;
  min-height: 58px;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--line);
}

.member-list__tag {
  padding: 3px 7px;
  color: #5e6b63;
  background: var(--green-100);
  border: 1px solid #dce6df;
  border-radius: 4px;
  font-size: 13px;
}

.skill-detail-meta__source {
  margin-left: auto;
  color: var(--muted);
  font-size: 14px;
}

.text-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--green-900);
  font-size: 14px;
  font-weight: 650;
}

.skill-content-layout {
  display: grid;
  grid-template-columns: minmax(190px, 240px) minmax(0, 1fr);
  gap: 14px;
  padding-top: 18px;
}

.skill-file-panel,
.skill-content-panel {
  min-width: 0;
  background: #ffffff;
  border: 1px solid var(--line);
  border-radius: 7px;
}

.skill-file-panel {
  align-self: start;
  padding: 9px;
}

.skill-file-panel__heading {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 8px 10px;
  color: #59675f;
  font-size: 14px;
  font-weight: 700;
}

.skill-file-item {
  display: block;
  width: 100%;
  padding: 8px;
  overflow: hidden;
  color: #68756d;
  background: transparent;
  border-radius: 4px;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-file-item:hover,
.skill-file-item.active {
  color: var(--green-900);
  background: var(--green-100);
}

.skill-content-panel__header {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}

.skill-content-panel__header > div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.skill-content-panel__header strong {
  overflow: hidden;
  color: #26332c;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-content {
  min-height: 520px;
  max-height: calc(100vh - 270px);
  margin: 0;
  padding: 18px;
  overflow: auto;
  color: #27352d;
  background: #fbfcfb;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.skill-content-warning {
  margin: 0;
  padding: 10px 14px;
  color: #8b5b26;
  background: #fff8ed;
  border-top: 1px solid #f2dfc4;
  font-size: 14px;
}

@media (max-width: 760px) {
  .skill-detail-meta__source {
    width: 100%;
    margin-left: 0;
  }

  .skill-content-layout {
    grid-template-columns: 1fr;
  }

  .skill-file-panel {
    display: flex;
    gap: 4px;
    overflow-x: auto;
  }

  .skill-file-panel__heading {
    flex: 0 0 auto;
  }

  .skill-file-item {
    width: auto;
    flex: 0 0 auto;
  }

  .skill-content {
    max-height: none;
  }
}
</style>
