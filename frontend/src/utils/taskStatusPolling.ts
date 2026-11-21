export function createLatestTaskRefresh<T>(
  currentId: () => string | undefined,
  load: (id: string) => Promise<T>,
  apply: (task: T) => void,
): () => Promise<void> {
  let sequence = 0
  let appliedSequence = 0
  return async () => {
    const id = currentId()
    if (!id) return
    const request = ++sequence
    const task = await load(id)
    if (request > appliedSequence && currentId() === id) {
      appliedSequence = request
      apply(task)
    }
  }
}

export function pollTaskStatus(
  refresh: () => Promise<void>,
  isFinished: () => boolean,
  onFinished: () => void,
): () => void {
  let stopped = false
  let inFlight = false
  const stop = () => {
    stopped = true
    window.clearInterval(timer)
  }
  const timer = window.setInterval(async () => {
    if (stopped || inFlight) return
    inFlight = true
    try {
      await refresh()
      if (!stopped && isFinished()) {
        stop()
        onFinished()
      }
    } catch {
      // Retain the timer and retry when the network becomes available.
    } finally {
      inFlight = false
    }
  }, 5000)
  return stop
}
