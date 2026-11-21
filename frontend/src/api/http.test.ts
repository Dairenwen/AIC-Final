import { describe, expect, it } from 'vitest'

import { apiUrl, normalizeApiBaseUrl, projectContextFromSearch, taskIdFromResponse, taskRecordFromResponse } from './http'

describe('projectContextFromSearch', () => {
  it('keeps project ownership when opening an Agent workspace', () => {
    expect(projectContextFromSearch('?task=task-1&project=project-42')).toBe('project-42')
  })

  it('returns null outside a project workspace', () => {
    expect(projectContextFromSearch('?task=task-1')).toBeNull()
    expect(projectContextFromSearch('?project=')).toBeNull()
  })
})

describe('API base URL normalization', () => {
  it('collapses duplicated deployment prefixes', () => {
    expect(normalizeApiBaseUrl('/api/v1/api/v1')).toBe('/api/v1')
    expect(normalizeApiBaseUrl('https://example.test/api/v1/api/v1/')).toBe('https://example.test/api/v1')
    expect(apiUrl('/tasks/demo/artifacts/figure-code-python')).not.toContain('/api/v1/api/v1')
  })
})

describe('task response compatibility', () => {
  it('extracts ids from nested proxy envelopes and legacy keys', () => {
    const response = { data: { result: { task: { task_uuid: 'abc-123', status: 'QUEUED' } } } }
    expect(taskIdFromResponse(response)).toBe('abc-123')
    expect(taskRecordFromResponse(response)).toEqual({ task_uuid: 'abc-123', status: 'QUEUED' })
  })

  it('does not mistake a history list for a newly created task', () => {
    const response = { data: [{ id: 'old-task', task_type: 'LITERATURE_SEARCH' }] }
    expect(taskIdFromResponse(response)).toBeNull()
    expect(taskRecordFromResponse(response)).toEqual({})
  })
})
