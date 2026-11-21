import { describe, expect, it } from 'vitest'

import { AGENT_PROMPT_TEMPLATES, getAgentPromptTemplate } from './agentPromptTemplates'

const agentCodes = [
  'literature_search',
  'manuscript_assistance',
  'innovation_point_generation',
  'paper_reading',
  'academic_compliance',
  'academic_translation',
  'reviewer_comments',
  'contribution_recommendation',
  'patent_drafting',
  'academic_figure',
  'arxiv_daily',
]

describe('agent prompt templates', () => {
  it.each(agentCodes)('provides a structured template for %s', (agentCode) => {
    const template = getAgentPromptTemplate(agentCode)
    expect(template).toContain('【')
    expect(template.split('\n').length).toBeGreaterThanOrEqual(5)
  })

  it('covers every supported Agent without duplicate template content', () => {
    expect(Object.keys(AGENT_PROMPT_TEMPLATES).sort()).toEqual([...agentCodes].sort())
    expect(new Set(Object.values(AGENT_PROMPT_TEMPLATES)).size).toBe(agentCodes.length)
  })

  it('provides a useful fallback for catalog extensions', () => {
    expect(getAgentPromptTemplate('future_agent')).toContain('任务目标')
  })
})
