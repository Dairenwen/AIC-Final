import { parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

import source from './ModelLibraryView.vue?raw'

describe('ModelLibraryView', () => {
  it('presents the platform models and lets users add their own', () => {
    const { descriptor } = parse(source, { filename: 'ModelLibraryView.vue' })
    const template = descriptor.template?.content || ''

    expect(template).toContain('模型库')
    expect(template).toContain('API 对话模型')
    expect(template).toContain('平台微调模型')
    expect(template).toContain('{{ platformModelName }}')
    expect(template).toContain('API 模型服务')
    expect(template).not.toContain('v-if="false')
    expect(template).toContain('添加模型')
    expect(template).toContain('保存并测试连接')
    expect(template).toContain('测试')
    expect(template).toContain('当前默认')
    expect(template).toContain('class="model-card-grid"')
  })

  it('loads and updates the account default dialogue model', () => {
    const { descriptor } = parse(source, { filename: 'ModelLibraryView.vue' })
    const script = descriptor.scriptSetup?.content || ''

    expect(script).toContain("value: 'api_model'")
    expect(script).toContain("getData<DefaultModelConfig>('/model-configs/default')")
    expect(script).toContain("http.post<{ data: DefaultModelConfig }>('/model-configs/default'")
  })

  it('supports rendering inside the personal-center panel', () => {
    const { descriptor } = parse(source, { filename: 'ModelLibraryView.vue' })
    const script = descriptor.scriptSetup?.content || ''
    const template = descriptor.template?.content || ''

    expect(script).toContain("defineProps<{ embedded?: boolean }>()")
    expect(template).toContain("'model-library-page--embedded': embedded")
  })
})
