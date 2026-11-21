import { describe, expect, it } from 'vitest'

import { glyphs } from '@/components/icons/glyphs'
import {
  agentPresentation,
  categoryLabel,
  skillIcon,
  statusLabel,
  toolPresentation,
  toolPresentationForRoute,
} from './presentation'

// The agent and tool codes the backend seeds.  A new one must get its own
// entry here, otherwise its page would open without a glyph or wording.
const AGENT_CODES = [
  'literature_search', 'manuscript_assistance', 'innovation_point_generation', 'paper_reading',
  'patent_drafting', 'academic_compliance', 'arxiv_daily', 'academic_figure', 'academic_translation',
  'reviewer_comments', 'contribution_recommendation',
]
const TOOL_CODES = ['markdown_to_docx', 'formula_image_to_latex', 'text_statistics', 'citation_formatter', 'table_converter']

describe('catalogue presentation', () => {
  it('covers every seeded agent and tool', () => {
    for (const code of AGENT_CODES) expect(agentPresentation[code], code).toBeDefined()
    for (const code of TOOL_CODES) expect(toolPresentation[code], code).toBeDefined()
  })

  it('gives every agent and tool a glyph of its own', () => {
    const icons = [...Object.values(agentPresentation), ...Object.values(toolPresentation)].map((entry) => entry.icon)
    expect(new Set(icons).size).toBe(icons.length)
    for (const icon of icons) expect(glyphs[icon], icon).toBeDefined()
  })

  it('writes for the reader, not about the implementation', () => {
    const text = [...Object.values(agentPresentation), ...Object.values(toolPresentation)]
      .map((entry) => Object.values(entry).join(' '))
      .join(' ')
    for (const word of ['LangGraph', 'Agent', '工作流', '赋能', '一站式', '智能化', '全流程']) {
      expect(text, word).not.toContain(word)
    }
  })

  it('finds a tool entry from its route', () => {
    expect(toolPresentationForRoute('/tools/table-converter')?.icon).toBe('table')
    expect(toolPresentationForRoute('/tools/unknown')).toBeUndefined()
  })

  it('picks a skill glyph from what the skill is about', () => {
    expect(skillIcon({ name: 'light-research-ethics', description: '', tags: [] })).toBe('ethics')
    expect(skillIcon({ name: 'venue-templates', description: 'LaTeX templates', tags: [] })).toBe('layout')
    expect(skillIcon({ name: 'x', description: '', tags: [] })).toBe('hexagon')
  })

  it('shows categories and states in one language', () => {
    expect(categoryLabel('development')).toBe('开发')
    expect(categoryLabel('科研')).toBe('科研')
    expect(statusLabel('APPROVED')).toBe('已上架')
  })
})
