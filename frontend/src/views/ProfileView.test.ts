import { parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

import source from './ProfileView.vue?raw'

describe('ProfileView model library panel', () => {
  it('switches the model library inside the account panel without navigation', () => {
    const { descriptor } = parse(source, { filename: 'ProfileView.vue' })
    const template = descriptor.template?.content || ''
    const script = descriptor.scriptSetup?.content || ''

    expect(script).toContain("'profile' | 'model' | 'password' | 'plan'")
    expect(template).toContain("@click=\"activeTab = 'model'\"")
    expect(template).toContain("activeTab === 'model'")
    expect(template).toContain('<ModelLibraryView embedded />')
    expect(template).not.toContain("router.push('/models')")
  })
})
