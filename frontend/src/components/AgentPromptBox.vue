<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowUp, FileText, FolderOpen, Link2, LoaderCircle, X } from 'lucide-vue-next'

import { getData } from '@/api/http'
import type { ModelConfig } from '@/types'

interface AgentPromptPayload {
  prompt: string
  model: string
  attachment: string | null
  link: string | null
  file: File | null
}

const prompt = defineModel<string>({ default: '' })
const props = withDefaults(defineProps<{
  busy?: boolean
  placeholder: string
  hint: string
  accept?: string
  allowPersonalModels?: boolean
  showModelSelector?: boolean
  showFilePicker?: boolean
  filePickerLabel?: string
}>(), {
  showFilePicker: true,
})
const emit = defineEmits<{
  submit: [payload: AgentPromptPayload]
}>()

const selectedFile = ref<File | null>(null)
const selectedLink = ref('')
const linkDraft = ref('')
const linkInputOpen = ref(false)
const selectedModel = ref('auto')
const personalModels = ref<ModelConfig[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

function chooseFile() {
  fileInput.value?.click()
}

function onFileChange(event: Event) {
  const files = (event.target as HTMLInputElement).files
  selectedFile.value = files?.[0] ?? null
}

function toggleLinkInput() {
  linkInputOpen.value = !linkInputOpen.value
  if (linkInputOpen.value && selectedLink.value) linkDraft.value = selectedLink.value
}

function confirmLink() {
  const value = normalizeLink(linkDraft.value)
  if (!value) {
    removeLink()
    linkInputOpen.value = false
    return
  }
  selectedLink.value = value
  linkDraft.value = value
  linkInputOpen.value = false
}

function removeLink() {
  selectedLink.value = ''
  linkDraft.value = ''
}

function removeFile() {
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
}

function normalizeLink(value: string) {
  const trimmed = value.trim()
  if (!trimmed) return ''
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  const arxivId = trimmed.match(/^(?:arxiv:)?((?:[a-z-]+\/\d{7})|(?:\d{4}\.\d{4,5}))(?:v\d+)?$/i)?.[1]
  if (arxivId) return `https://arxiv.org/abs/${arxivId}`
  return `https://${trimmed}`
}

function submit() {
  const content = prompt.value.trim()
  if (!content || props.busy) return
  emit('submit', {
    prompt: content,
    model: selectedModel.value,
    attachment: selectedFile.value?.name || null,
    link: selectedLink.value || null,
    file: selectedFile.value,
  })
}

function handlePromptEnter(event: KeyboardEvent) {
  if (event.isComposing) return
  event.preventDefault()
  submit()
}

onMounted(async () => {
  if (!props.allowPersonalModels) return
  try {
    const items = await getData<ModelConfig[]>('/model-configs')
    personalModels.value = items.filter((item) => item.status === 'ACTIVE' && item.model_type_code === 'chat')
  } catch {
    personalModels.value = []
  }
})
</script>

<template>
  <div class="agent-prompt-box">
    <textarea
      v-model="prompt"
      rows="4"
      :placeholder="placeholder"
      @keydown.enter.exact="handlePromptEnter"
      @keydown.ctrl.enter.prevent="submit"
      @keydown.meta.enter.prevent="submit"
    ></textarea>

    <div v-if="showFilePicker !== false && selectedFile" class="selected-file agent-selected-file">
      <FileText :size="14" />
      <span>{{ selectedFile.name }}</span>
      <button type="button" aria-label="移除文件" title="移除文件" @click="removeFile"><X :size="13" /></button>
    </div>

    <div v-if="selectedLink" class="selected-file agent-selected-file">
      <Link2 :size="14" />
      <span>{{ selectedLink }}</span>
      <button type="button" aria-label="移除链接" title="移除链接" @click="removeLink"><X :size="13" /></button>
    </div>

    <div v-if="linkInputOpen" class="agent-link-input">
      <Link2 :size="15" />
      <input
        v-model="linkDraft"
        type="url"
        placeholder="粘贴论文、网页或数据集链接"
        @keydown.enter.prevent="confirmLink"
        @keydown.esc.prevent="linkInputOpen = false"
      />
      <button type="button" @click="confirmLink">添加</button>
    </div>

    <div class="agent-prompt-box__footer">
      <div class="composer__tools">
        <input v-if="showFilePicker !== false" ref="fileInput" class="sr-only" type="file" :accept="accept || '.pdf,.doc,.docx,.txt,.md,.csv,.xlsx'" @change="onFileChange" />
        <button
          v-if="showFilePicker !== false"
          class="icon-button"
          :class="{ 'agent-file-button--labeled': filePickerLabel }"
          type="button"
          :aria-label="filePickerLabel || '添加文件'"
          :title="filePickerLabel || '添加文件'"
          @click="chooseFile"
        >
          <FolderOpen :size="17" />
          <span v-if="filePickerLabel">{{ filePickerLabel }}</span>
        </button>
        <button class="icon-button" type="button" aria-label="添加链接" title="添加链接" @click="toggleLinkInput">
          <Link2 :size="16" />
        </button>
        <select v-if="showModelSelector !== false" v-model="selectedModel" aria-label="选择模型">
          <option value="auto">自动选择模型</option>
          <option value="vertical_domain">平台通用模型</option>
          <optgroup v-if="allowPersonalModels && personalModels.length" label="我的模型">
            <option v-for="item in personalModels" :key="item.id" :value="`model_config:${item.id}`">{{ item.name }} · {{ item.model_name }}</option>
          </optgroup>
        </select>
      </div>
      <span class="agent-prompt-box__hint">{{ hint }}</span>
      <button class="send-button" type="button" :disabled="!prompt.trim() || busy" aria-label="发送任务" title="发送任务" @click="submit">
        <LoaderCircle v-if="busy" class="spin" :size="17" />
        <ArrowUp v-else :size="17" />
      </button>
    </div>
  </div>
</template>
