import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { monitorPatentTask } from './patentTaskMonitor'

class FakeEventSource {
  listeners = new Map<string, (event: Event) => void>()
  onerror: (() => void) | null = null
  close = vi.fn()

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void)
  }

  emit(type: string, sequence: number, progress: number) {
    this.listeners.get(type)?.({ data: JSON.stringify({ type, sequence, progress, message: type }) } as MessageEvent)
  }
}

describe('patent task progress monitoring', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('window', { setInterval, clearInterval })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('keeps listening when an old selection event is replayed after the task resumes', async () => {
    const source = new FakeEventSource()
    const refresh = vi.fn().mockResolvedValueOnce('RUNNING').mockResolvedValueOnce('SUCCEEDED')
    const received = vi.fn()
    monitorPatentTask(source as unknown as EventSource, received, refresh)

    source.emit('patent.selection_required', 4, 35)
    await Promise.resolve()
    expect(source.close).not.toHaveBeenCalled()
    source.emit('task.completed', 8, 100)
    await Promise.resolve()
    expect(received).toHaveBeenCalledTimes(2)
    expect(source.close).toHaveBeenCalledOnce()
  })

  it('stops at an actual wait for user selection', async () => {
    const source = new FakeEventSource()
    monitorPatentTask(source as unknown as EventSource, vi.fn(), vi.fn().mockResolvedValue('WAITING_INPUT'))
    source.emit('patent.selection_required', 4, 35)
    await Promise.resolve()
    expect(source.close).toHaveBeenCalledOnce()
  })

  it('recovers a completed status by polling after a transient refresh failure', async () => {
    const source = new FakeEventSource()
    const refresh = vi.fn().mockRejectedValueOnce(new Error('temporary')).mockResolvedValueOnce('SUCCEEDED')
    monitorPatentTask(source as unknown as EventSource, vi.fn(), refresh)
    source.onerror?.()
    await Promise.resolve()
    expect(source.close).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    expect(source.close).toHaveBeenCalledOnce()
  })
})
