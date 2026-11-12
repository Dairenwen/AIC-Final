import { parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

import source from './HomeView.vue?raw'

describe('HomeView focused conversation layout', () => {
  it('switches from the welcome content to a focused conversation surface', () => {
    const { descriptor } = parse(source, { filename: 'HomeView.vue' })
    const script = descriptor.scriptSetup?.content || ''
    const template = descriptor.template?.content || ''

    expect(script).toContain('const chatMode = ref(false)')
    expect(template).toContain("'home-view--chat-mode': chatMode")
    expect(template).toContain('@chat-mode-change="setChatMode"')
    expect(template).toContain('class="home-hero__intro"')
  })

  it('links the guide to the Feishu documentation and exposes the theme toggle', () => {
    const { descriptor } = parse(source, { filename: 'HomeView.vue' })
    const script = descriptor.scriptSetup?.content || ''
    const template = descriptor.template?.content || ''

    expect(template).toContain('https://scnss29ndazj.feishu.cn/wiki/REdJwLqwziE7likYvO0cxC4onCb?from=from_copylink')
    expect(template).toContain('target="_blank"')
    expect(template).toContain('@click="toggleTheme"')
    expect(script).toContain('applyTheme(theme.value)')
  })
})
