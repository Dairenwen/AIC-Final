import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createLatestTaskRefresh, pollTaskStatus } from './taskStatusPolling'

describe('task status synchronization', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('window', { setInterval, clearInterval })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('does not replace a completed response with an older running response', async () => {
    const pending: Array<(value: string) => void> = []
    const apply = vi.fn()
    const refresh = createLatestTaskRefresh(
      () => 'task-1',
      () => new Promise<string>((resolve) => pending.push(resolve)),
      apply,
    )
    const first = refresh()
    const second = refresh()
    pending[1]('SUCCEEDED')
    await second
    pending[0]('RUNNING')
    await first
    expect(apply.mock.calls).toEqual([['SUCCEEDED']])
  })

  it('ignores a response after the user opens a different task', async () => {
    let active = 'task-1'
    let resolve!: (value: string) => void
    const apply = vi.fn()
    const refresh = createLatestTaskRefresh(
      () => active,
      () => new Promise<string>((done) => { resolve = done }),
      apply,
    )
    const request = refresh()
    active = 'task-2'
    resolve('SUCCEEDED')
    await request
    expect(apply).not.toHaveBeenCalled()
  })

  it('retries after a network error and stops once the task finishes', async () => {
    const refresh = vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(undefined)
    const finished = vi.fn().mockReturnValue(true)
    const close = vi.fn()
    pollTaskStatus(refresh, finished, close)
    await vi.advanceTimersByTimeAsync(5000)
    expect(close).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    expect(close).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(5000)
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('does not overlap status polls when a request is slow', async () => {
    let resolve!: () => void
    const refresh = vi.fn(() => new Promise<void>((done) => { resolve = done }))
    const stop = pollTaskStatus(refresh, () => false, vi.fn())
    await vi.advanceTimersByTimeAsync(15000)
    expect(refresh).toHaveBeenCalledTimes(1)
    resolve()
    await Promise.resolve()
    stop()
  })
})
