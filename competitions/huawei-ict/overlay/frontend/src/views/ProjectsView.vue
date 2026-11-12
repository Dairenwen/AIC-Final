<script setup lang="ts">
import ZyIcon from '@/components/icons/ZyIcon.vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, CalendarDays, Plus, Search, Target, X } from 'lucide-vue-next'

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
        <h1>科研项目</h1>
        <p>把同一个课题的文档、对话和任务放在一起。</p>
      </div>
      <button class="primary-button" type="button" @click="createOpen = true"><Plus :size="16" />新建项目</button>
    </header>

    <div class="projects-toolbar">
      <label><Search :size="16" /><input v-model="query" type="search" placeholder="搜索项目" /></label>
      <span>{{ filteredProjects.length }} 个项目</span>
    </div>

    <div v-if="loading" class="projects-empty">正在加载</div>
    <div v-else-if="!filteredProjects.length" class="projects-empty">
      <ZyIcon class="zy-icon--draw" name="projects" :size="32" />
      <strong>{{ query ? '没有找到项目' : '还没有项目' }}</strong>
      <p>{{ query ? '换个关键词试试。' : '建一个项目，之后这个课题的文档、对话和任务都会留在里面。' }}</p>
      <button v-if="!query" class="primary-button" type="button" @click="createOpen = true"><Plus :size="16" />新建项目</button>
    </div>
    <section v-else class="project-grid stagger" aria-label="科研项目列表">
      <RouterLink v-for="project in filteredProjects" :key="project.id" class="project-card lift nudge-arrow" :to="`/projects/${project.id}`">
        <div class="project-card__top"><span><ZyIcon name="projects" :size="19" /></span><small>{{ project.role === 'OWNER' ? '我负责' : project.role === 'EDITOR' ? '协作编辑' : '只读成员' }}</small></div>
        <h2>{{ project.name }}</h2>
        <p>{{ project.description || '没有填写说明' }}</p>
        <div class="project-card__goal"><Target :size="14" /><span>{{ project.research_goal || '没有填写研究目标' }}</span></div>
        <footer><span><CalendarDays :size="14" />{{ formatDate(project.updated_at) }}</span><ArrowRight :size="16" /></footer>
      </RouterLink>
    </section>

    <div v-if="createOpen" class="dialog-backdrop" @click.self="createOpen = false">
      <form class="project-dialog" @submit.prevent="createProject">
        <header><div><h2>新建项目</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="createOpen = false"><X :size="18" /></button></header>
        <label>项目名称<input v-model="form.name" autofocus maxlength="160" placeholder="例如：多智能体科研助手的可信性" /></label>
        <label>项目说明<textarea v-model="form.description" rows="3" placeholder="选填：研究背景和范围"></textarea></label>
        <label>研究目标<textarea v-model="form.research_goal" rows="3" placeholder="选填：想验证什么，预期得到什么"></textarea></label>
        <p v-if="errorMessage" class="form-error">{{ errorMessage }}</p>
        <footer><button class="text-button" type="button" @click="createOpen = false">取消</button><button class="primary-button" type="submit" :disabled="!form.name.trim() || creating">{{ creating ? '正在创建' : '创建' }}</button></footer>
      </form>
    </div>
  </div>
</template>
