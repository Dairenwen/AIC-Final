<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, CalendarDays, FolderKanban, Plus, Search, Target, X } from 'lucide-vue-next'

import { getData, http } from '@/api/http'
import type { ResearchProject } from '@/types'

const router = useRouter()
const projects = ref<ResearchProject[]>([])
const query = ref('')
const loading = ref(true)
const creating = ref(false)
const createOpen = ref(false)
const form = ref({ name: '', description: '', research_goal: '' })
const errorMessage = ref('')

const filteredProjects = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return projects.value.filter((item) => !keyword || `${item.name}${item.description}${item.research_goal}`.toLowerCase().includes(keyword))
})

async function loadProjects() {
  loading.value = true
  try {
    projects.value = await getData<ResearchProject[]>('/projects')
  } finally {
    loading.value = false
  }
}

async function createProject() {
  if (!form.value.name.trim() || creating.value) return
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await http.post('/projects', form.value)
    const project = response.data.data as ResearchProject
    createOpen.value = false
    form.value = { name: '', description: '', research_goal: '' }
    await router.push(`/projects/${project.id}`)
  } catch (error) {
    const value = error as { response?: { data?: { error?: { message?: string } } } }
    errorMessage.value = value.response?.data?.error?.message || '项目创建失败'
  } finally {
    creating.value = false
  }
}

function formatDate(value?: string) {
  return value ? new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(new Date(value)) : '刚刚'
}

onMounted(loadProjects)
</script>

<template>
  <div class="projects-view">
    <header class="projects-header">
      <div>
        <p class="eyebrow">RESEARCH PROJECTS</p>
        <h1>科研项目</h1>
        <p>围绕同一研究目标沉淀文档、对话、任务与成果。</p>
      </div>
      <button class="primary-button" type="button" @click="createOpen = true"><Plus :size="16" />新建项目</button>
    </header>

    <div class="projects-toolbar">
      <label><Search :size="16" /><input v-model="query" type="search" placeholder="搜索项目、研究目标" /></label>
      <span>{{ filteredProjects.length }} 个项目</span>
    </div>

    <div v-if="loading" class="projects-empty">正在加载项目...</div>
    <div v-else-if="!filteredProjects.length" class="projects-empty">
      <FolderKanban :size="30" />
      <strong>{{ query ? '没有匹配的项目' : '从一个明确的研究目标开始' }}</strong>
      <p>{{ query ? '尝试调整搜索关键词。' : '创建项目后，文档、AI 对话和 Agent 任务会持续保留在同一工作区。' }}</p>
      <button v-if="!query" class="primary-button" type="button" @click="createOpen = true"><Plus :size="16" />新建项目</button>
    </div>
    <section v-else class="project-grid" aria-label="科研项目列表">
      <RouterLink v-for="project in filteredProjects" :key="project.id" class="project-card" :to="`/projects/${project.id}`">
        <div class="project-card__top"><span><FolderKanban :size="17" /></span><small>{{ project.role === 'OWNER' ? '我负责' : project.role === 'EDITOR' ? '协作编辑' : '只读成员' }}</small></div>
        <h2>{{ project.name }}</h2>
        <p>{{ project.description || '暂无项目说明' }}</p>
        <div class="project-card__goal"><Target :size="14" /><span>{{ project.research_goal || '待补充研究目标' }}</span></div>
        <footer><span><CalendarDays :size="14" />{{ formatDate(project.updated_at) }}</span><ArrowRight :size="16" /></footer>
      </RouterLink>
    </section>

    <div v-if="createOpen" class="dialog-backdrop" @click.self="createOpen = false">
      <form class="project-dialog" @submit.prevent="createProject">
        <header><div><small>NEW PROJECT</small><h2>建立科研项目</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="createOpen = false"><X :size="18" /></button></header>
        <label>项目名称<input v-model="form.name" autofocus maxlength="160" placeholder="例如：多智能体科研助手可信性研究" /></label>
        <label>项目说明<textarea v-model="form.description" rows="3" placeholder="研究背景、范围与协作约定"></textarea></label>
        <label>研究目标<textarea v-model="form.research_goal" rows="3" placeholder="希望验证的问题与预期成果"></textarea></label>
        <p v-if="errorMessage" class="form-error">{{ errorMessage }}</p>
        <footer><button class="text-button" type="button" @click="createOpen = false">取消</button><button class="primary-button" type="submit" :disabled="!form.name.trim() || creating">{{ creating ? '创建中...' : '创建项目' }}</button></footer>
      </form>
    </div>
  </div>
</template>
