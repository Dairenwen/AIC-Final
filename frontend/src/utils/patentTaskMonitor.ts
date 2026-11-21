export interface PatentTaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
}

const EVENT_TYPES = [
  'task.started', 'patent.materials_ready', 'patent.candidates_started', 'patent.selection_required',
  'patent.selection_accepted', 'patent.drafting_started', 'patent.artifacts_ready', 'task.completed', 'task.failed',
]

export function monitorPatentTask(
  source: EventSource,
  onEvent: (event: PatentTaskEvent) => void,
  refresh: () => Promise<string | undefined>,
): () => void {
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    source.close()
    window.clearInterval(timer)
  }
  const sync = async () => {
    try {
      const status = await refresh()
      if (!closed && status && ['SUCCEEDED', 'FAILED', 'CANCELED', 'WAITING_INPUT'].includes(status)) close()
    } catch {
      // EventSource reconnects automatically; polling retries transient failures.
    }
  }
  const handle = (event: Event) => {
    if (closed) return
    onEvent(JSON.parse((event as MessageEvent).data) as PatentTaskEvent)
    void sync()
  }
  EVENT_TYPES.forEach((eventType) => source.addEventListener(eventType, handle))
  source.onerror = () => { void sync() }
  const timer = window.setInterval(() => { void sync() }, 5000)
  return close
}
