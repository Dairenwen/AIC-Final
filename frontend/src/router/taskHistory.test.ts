import { describe, expect, it } from 'vitest'

import { taskHistoryLocation } from './taskHistory'

describe('taskHistoryLocation', () => {
  it.each([
    ['literature_search', 'literature-search'],
    ['manuscript_assistance', 'manuscript-assistance'],
    ['innovation_point_generation', 'innovation-point-generation'],
    ['paper_reading', 'paper-reading'],
    ['academic_compliance', 'academic-compliance'],
    ['academic_translation', 'academic-translation'],
    ['patent_drafting', 'patent-drafting'],
    ['academic_figure', 'academic-figure'],
    ['arxiv_daily', 'academic-daily'],
  ])('routes %s history to its task workspace', (agentCode, name) => {
    expect(taskHistoryLocation({ id: 'task-123', agentCode })).toEqual({
      name,
      query: { task: 'task-123' },
    })
  })

  it('falls back to the agent catalog for an unsupported task type', () => {
    expect(taskHistoryLocation({ id: 'task-123', agentCode: 'unknown' })).toEqual({ name: 'agents' })
  })
})
