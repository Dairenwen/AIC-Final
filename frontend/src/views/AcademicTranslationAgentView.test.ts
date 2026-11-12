import { describe, expect, it } from 'vitest'

import source from './AcademicTranslationAgentView.vue?raw'

describe('AcademicTranslationAgentView upload controls', () => {
  it('explicitly exposes a labeled local document picker', () => {
    expect(source).toContain(':show-file-picker="true"')
    expect(source).toContain('file-picker-label="上传文档"')
  })
})
