import { describe, expect, it } from 'vitest'

import { parseSseBuffer } from './chatStream'

describe('parseSseBuffer', () => {
  it('returns complete events and keeps the unfinished tail', () => {
    const { events, rest } = parseSseBuffer('event: delta\ndata: {"text":"你"}\n\nevent: delta\ndata: {"te')

    expect(events).toEqual([{ event: 'delta', data: '{"text":"你"}' }])
    expect(rest).toBe('event: delta\ndata: {"te')
  })

  it('handles CRLF separators and events without an explicit name', () => {
    const { events, rest } = parseSseBuffer('data: {"a":1}\r\n\r\nevent: done\r\ndata: {"b":2}\r\n\r\n')

    expect(events).toEqual([
      { event: 'message', data: '{"a":1}' },
      { event: 'done', data: '{"b":2}' },
    ])
    expect(rest).toBe('')
  })

  it('ignores comment-only keep-alive blocks', () => {
    expect(parseSseBuffer(': ping\n\n').events).toEqual([])
  })
})
