<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getData } from '@/api/http'
import { categoryLabel, skillIcon } from '@/catalog/presentation'
import EmptyNotice from '@/components/EmptyNotice.vue'
import ZyIcon from '@/components/icons/ZyIcon.vue'
import type { CatalogItem, SkillDetail } from '@/types'
import { renderMarkdown } from '@/utils/renderMarkdown'

const route = useRoute()
const router = useRouter()
const detail = ref<SkillDetail | null>(null)
const loading = ref(true)
const loadError = ref(false)
const selectedFile = ref('')
const copied = ref(false)
const showSource = ref(false)

const downloaded = computed(() => detail.value?.downloadStatus === 'DOWNLOADED')
const hasFiles = computed(() => (detail.value?.files.length ?? 0) > 0)
const activeContent = computed(() => {
  if (!detail.value || !selectedFile.value) return detail.value?.fullContent || detail.value?.description || ''
  return detail.value.files.find((file) => file.path === selectedFile.value)?.content || ''
})
// Markdown and plain prose are rendered as a document; scripts and data
// files keep their line structure in a code block.
const isProse = computed(() => !selectedFile.value || /\.(md|markdown|txt)$/i.test(selectedFile.value))
const renderedContent = computed(() => renderMarkdown(readableText(activeContent.value)))
const icon = computed(() => (detail.value ? skillIcon(detail.value as unknown as CatalogItem) : 'skills'))
// The crawled description is one long run of text.  Break it at the phrases
// these skill cards use to start a new part so it reads as short paragraphs.
const introParagraphs = computed(() => splitIntro(detail.value?.description || ''))
const showIntro = computed(() => downloaded.value && introParagraphs.value.length > 0)
const sourceLabel = computed(() => {
  if (!detail.value) return ''
  return downloaded.value ? `已收录 ${detail.value.fileCount} 个文件` : '仅收录简介'
})

function readableText(value: string) {
  // Descriptions saved without line breaks become one paragraph per part.
  if (value.includes('\n')) return value
  return splitIntro(value).join('\n\n')
}

function splitIntro(value: string) {
  const text = value.replace(/\r/g, '').trim()
  if (!text) return []
  if (text.includes('\n')) return text.split(/\n+/).map((line) => line.trim()).filter(Boolean)
  return text
    .replace(/(。|；)\s*(何时用|触发词|核心纪律|不要用于|当用户|适用场景|使用场景|注意)/g, '$1\n$2')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

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
  window.setTimeout(() => {
    copied.value = false
  }, 1600)
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/skills')
}

onMounted(loadSkill)
</script>

<template>
  <div v-if="loading" class="workspace-page skill-detail-page">
    <div class="table-loading">正在读取</div>
  </div>
  <div v-else-if="loadError || !detail" class="workspace-page skill-detail-page">
    <EmptyNotice message="内容没有读取出来，请返回技能库再试一次。" />
  </div>
  <div v-else class="workspace-page skill-detail-page">
    <button class="skill-back" type="button" @click="goBack"><ZyIcon name="chevronRight" :size="14" />科研技能库</button>

    <header class="skill-hero">
      <span class="skill-hero__icon"><ZyIcon :name="icon" :size="26" /></span>
      <div class="skill-hero__text">
        <h1>{{ detail.name }}</h1>
        <p>
          <span>{{ categoryLabel(detail.category) || '科研技能' }}</span>
          <span>{{ detail.author || '开放技能库' }}</span>
          <span>{{ sourceLabel }}</span>
        </p>
      </div>
      <div class="skill-hero__actions">
        <a v-if="detail.sourceUrl" class="secondary-button" :href="detail.sourceUrl" target="_blank" rel="noreferrer">来源链接<ZyIcon name="arrowRight" :size="14" /></a>
        <button class="primary-button" type="button" :disabled="!activeContent" @click="copyContent">{{ copied ? '已复制' : '复制内容' }}</button>
      </div>
    </header>

    <div v-if="detail.tags.length" class="skill-tags">
      <span v-for="tag in detail.tags" :key="tag">{{ tag }}</span>
    </div>

    <section v-if="showIntro" class="skill-card skill-intro">
      <h2>简介</h2>
      <p v-for="paragraph in introParagraphs" :key="paragraph">{{ paragraph.replace(/\*\*/g, '') }}</p>
    </section>

    <section class="skill-content-layout" :class="{ 'skill-content-layout--single': !hasFiles }">
      <aside v-if="hasFiles" class="skill-card skill-file-panel" aria-label="技能文件">
        <div class="skill-file-panel__heading">文件目录</div>
        <button type="button" class="skill-file-item" :class="{ active: !selectedFile }" @click="selectedFile = ''">完整内容</button>
        <button
          v-for="file in detail.files"
          :key="file.path"
          type="button"
          class="skill-file-item"
          :class="{ active: selectedFile === file.path }"
          :title="file.path"
          @click="selectedFile = file.path"
        >
          {{ file.path }}
        </button>
      </aside>

      <article class="skill-card skill-content-panel">
        <header class="skill-content-panel__header">
          <strong>{{ selectedFile || (downloaded ? '完整内容' : '技能说明') }}</strong>
          <label v-if="isProse" class="skill-source-toggle"><input v-model="showSource" type="checkbox" />显示原文</label>
        </header>
        <div v-if="isProse && !showSource" class="skill-content skill-content--prose markdown-body" v-html="renderedContent || '暂无可展示内容'"></div>
        <pre v-else class="skill-content skill-content--code">{{ activeContent || '暂无可展示内容' }}</pre>
        <p v-if="!downloaded && !detail.downloadError" class="skill-content-note">这个技能目前只收录了说明文字，完整文件请到来源链接查看。</p>
        <p v-if="detail.downloadError" class="skill-content-warning">下载失败：{{ detail.downloadError }}</p>
      </article>
    </section>
  </div>
</template>

<style scoped>
.skill-back {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-bottom: var(--space-4);
  padding: 0;
  color: var(--text-tertiary);
  background: transparent;
  font-size: var(--type-footnote-size);
}

.skill-back .zy-icon {
  transform: rotate(180deg);
}

.skill-back:hover {
  color: var(--accent-text);
}

.skill-hero {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.skill-hero__icon {
  display: inline-flex;
  width: 3.25rem;
  height: 3.25rem;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  color: var(--accent-text);
  background: var(--accent-tint);
  border-radius: var(--radius-card);
}

.skill-hero__text {
  min-width: 0;
  flex: 1 1 18rem;
}

.skill-hero__text h1 {
  margin: 0;
  font-size: var(--type-title1-size);
  line-height: var(--type-title1-line);
  letter-spacing: var(--type-title1-track);
  overflow-wrap: anywhere;
}

.skill-hero__text p {
  display: flex;
  gap: var(--space-2) var(--space-4);
  flex-wrap: wrap;
  margin: var(--space-2) 0 0;
  color: var(--text-tertiary);
  font-size: var(--type-footnote-size);
}

.skill-hero__text p span + span::before {
  margin-right: var(--space-4);
  color: var(--border-default);
  content: '|';
}

.skill-hero__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.skill-tags {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-top: var(--space-5);
}

.skill-tags span {
  padding: 0.1875rem 0.625rem;
  color: var(--text-secondary);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-pill);
  font-size: var(--type-caption-size);
  line-height: 1.6;
}

.skill-card {
  min-width: 0;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-panel);
}

.skill-intro {
  margin-top: var(--space-5);
  padding: var(--space-5) var(--space-6);
}

.skill-intro h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--type-callout-size);
}

.skill-intro p {
  max-width: 88ch;
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--type-callout-size);
  line-height: 1.85;
  overflow-wrap: anywhere;
}

.skill-intro p + p {
  margin-top: var(--space-3);
}

.skill-content-layout {
  display: grid;
  grid-template-columns: 15rem minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
  margin-top: var(--space-5);
}

.skill-content-layout--single {
  grid-template-columns: minmax(0, 1fr);
}

.skill-file-panel {
  position: sticky;
  top: var(--space-5);
  max-height: calc(100vh - 3rem);
  padding: var(--space-3);
  overflow-y: auto;
}

.skill-file-panel__heading {
  padding: var(--space-1) var(--space-3) var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--type-caption-size);
  letter-spacing: 0.04em;
}

.skill-file-item {
  display: block;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  overflow: hidden;
  color: var(--text-secondary);
  background: transparent;
  border-radius: 0.5rem;
  font-size: var(--type-footnote-size);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-file-item:hover {
  color: var(--text-primary);
  background: var(--bg-sunken);
}

.skill-file-item.active {
  color: var(--accent-text);
  background: var(--accent-tint);
  font-weight: var(--weight-semibold);
}

.skill-content-panel {
  overflow: hidden;
}

.skill-content-panel__header {
  display: flex;
  min-height: 3.25rem;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 0 var(--space-6);
  border-bottom: 1px solid var(--border-hairline);
}

.skill-content-panel__header strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--type-callout-size);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-source-toggle {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--type-footnote-size);
  cursor: pointer;
}

.skill-content {
  margin: 0;
  padding: var(--space-6);
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.skill-content--prose {
  max-width: 92ch;
  font-size: var(--type-callout-size);
  line-height: 1.85;
}

.skill-content--prose :deep(p) {
  margin: 0 0 0.9em;
}

.skill-content--prose :deep(h1),
.skill-content--prose :deep(h2),
.skill-content--prose :deep(h3),
.skill-content--prose :deep(h4) {
  margin: 1.5em 0 0.6em;
  line-height: 1.4;
}

.skill-content--prose :deep(h1) {
  font-size: 1.35rem;
}

.skill-content--prose :deep(h2) {
  font-size: 1.2rem;
}

.skill-content--prose :deep(h3),
.skill-content--prose :deep(h4) {
  font-size: 1.05rem;
}

.skill-content--prose :deep(> :first-child) {
  margin-top: 0;
}

.skill-content--prose :deep(pre) {
  margin: 1em 0;
  padding: var(--space-4);
  overflow-x: auto;
  background: var(--bg-sunken);
  border-radius: 0.625rem;
  font-size: 0.8125rem;
  line-height: 1.65;
}

.skill-content--prose :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.875em;
}

.skill-content--prose :deep(:not(pre) > code) {
  padding: 0.1em 0.35em;
  background: var(--bg-sunken);
  border-radius: 0.25rem;
}

.skill-content--prose :deep(a) {
  color: var(--accent-text);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.skill-content--prose :deep(blockquote) {
  margin: 1em 0;
  padding: 0.2em 0 0.2em 1em;
  color: var(--text-secondary);
  border-left: 3px solid var(--border-default);
}

.skill-content--code {
  max-height: calc(100vh - 14rem);
  overflow: auto;
  background: var(--bg-sunken);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.8125rem;
  line-height: 1.7;
  white-space: pre-wrap;
}

.skill-content-note,
.skill-content-warning {
  margin: 0;
  padding: var(--space-3) var(--space-6);
  border-top: 1px solid var(--border-hairline);
  font-size: var(--type-footnote-size);
}

.skill-content-note {
  color: var(--text-tertiary);
}

.skill-content-warning {
  color: var(--warning-text);
  background: var(--warning-tint);
}

@media (max-width: 860px) {
  .skill-content-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .skill-file-panel {
    position: static;
    display: flex;
    max-height: none;
    gap: var(--space-1);
    overflow-x: auto;
  }

  .skill-file-panel__heading {
    display: none;
  }

  .skill-file-item {
    width: auto;
    flex: 0 0 auto;
  }

  .skill-content,
  .skill-intro {
    padding: var(--space-4);
  }

  .skill-content-panel__header {
    padding: 0 var(--space-4);
  }
}
</style>
