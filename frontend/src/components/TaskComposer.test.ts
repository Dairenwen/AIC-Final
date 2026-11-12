import { parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

import source from './TaskComposer.vue?raw'

describe('TaskComposer file and knowledge-base entry points', () => {
  it('enables knowledge-base QA and routes file picking to the file icon', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const template = descriptor.template?.content || ''

    expect(template).toContain('知识库问答')
    expect(template).toContain('@click="selectKnowledgeBase"')
    expect(template).not.toContain('知识库问答功能暂未实现')
    expect(template).toContain('class="rag-evidence-list"')
    expect(template).toContain('class="rag-answer-details"')
    expect(template).toContain('renderMarkdown(message.content)')
    expect(template).toContain('class="icon-button composer-file-button"')
    expect(template).toContain('aria-label="添加文件"')
    expect(template).toContain('@click="chooseFile"')
    expect(template).not.toContain('aria-label="添加链接"')
  })

  it('posts knowledge-base questions to the RAG answer endpoint', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const script = descriptor.scriptSetup?.content || ''

    expect(script).toContain("http.post<{ data: RagAnswer }>('/rag/answers'")
    expect(script).toContain('document_ids: []')
    expect(script).toContain('if (knowledgeBaseMode.value)')
  })

  it('loads the configured default and shows the model returned by both answer APIs', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const script = descriptor.scriptSetup?.content || ''

    expect(script).toContain("const model = ref('api_model')")
    expect(script).toContain("getData<DefaultModelConfig>('/model-configs/default')")
    expect(script).toContain("defaultModelValue.value = 'api_model'")
    expect(script).toContain("const PLATFORM_DISPLAY_MODEL = 'qwen3.6-27B-sft'")
    expect(script).toContain('model: modelDisplayName(model.value)')
    expect(script).not.toContain("if (model.value.startsWith('model_config:')) model.value = 'auto'")
  })

  it('keeps the model control compact and shows only personal model names', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const template = descriptor.template?.content || ''

    expect(template).toContain('class="composer-model-select"')
    expect(template).toContain('{{ item.name }}')
    expect(template).not.toContain('{{ item.name }} · {{ item.model_name }}')
    expect(descriptor.scriptSetup?.content || '').toContain("?.name || '个人模型'")
  })

  it('reports chat-mode changes so the home page can switch to an embedded split layout', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const script = descriptor.scriptSetup?.content || ''

    expect(script).toContain('chatModeChange: [active: boolean]')
    expect(script).toContain("watch(isChatMode, (active) => emit('chatModeChange', active), { immediate: true })")
    expect(script).toContain('chatList.value.scrollTop = chatList.value.scrollHeight')
  })

  it('shows the AI-generated notice on every completed assistant reply', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const template = descriptor.template?.content || ''

    expect(template).toContain("message.role === 'assistant' && !message.pending")
    expect(template).toContain('class="composer-chat__disclaimer"')
    expect(template).toContain('内容由智研ai生成')
  })

  it('renders assistant Markdown and exposes reply actions', () => {
    const { descriptor } = parse(source, { filename: 'TaskComposer.vue' })
    const script = descriptor.scriptSetup?.content || ''
    const template = descriptor.template?.content || ''

    expect(script).toContain("import { renderMarkdown } from '@/utils/renderMarkdown'")
    expect(template).toContain('v-html="renderMarkdown(message.content)"')
    expect(template).toContain('aria-label="复制回答"')
    expect(template).toContain('aria-label="分享回答"')
    expect(template).toContain('aria-label="点赞回答"')
    expect(script).toContain('navigator.clipboard.writeText(message.content)')
    expect(script).toContain('navigator.share')
  })
})
