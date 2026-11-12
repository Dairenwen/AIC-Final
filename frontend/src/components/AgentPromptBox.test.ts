import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

import source from './AgentPromptBox.vue?raw'

describe('AgentPromptBox props', () => {
  it('shows the file picker when showFilePicker is omitted', () => {
    const filename = 'AgentPromptBox.vue'
    const { descriptor } = parse(source, { filename })
    const compiled = compileScript(descriptor, { id: 'agent-prompt-box-test' })

    expect(compiled.content).toContain('showFilePicker: { type: Boolean, required: false, default: true }')
  })
})
