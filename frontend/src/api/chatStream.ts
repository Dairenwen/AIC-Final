import { clearStoredAuth, getCsrfToken } from '@/auth/storage'

import { apiUrl } from './http'

export interface ChatStreamDone {
  model?: string
  message_id?: string
  conversation_id?: string
  usage?: Record<string, unknown>
}

export interface ChatStreamHandlers {
  onMeta?: (data: { model?: string }) => void
  onReasoning?: (text: string) => void
  onDelta?: (text: string) => void
  onDone?: (data: ChatStreamDone) => void
}

export interface SseEvent {
  event: string
  data: string
}

/**
 * Split a text buffer into complete server-sent events.
 * Returns the parsed events and the unfinished tail to keep for the next chunk.
 */
export function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: SseEvent[] = []
  for (const block of blocks) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    }
    if (dataLines.length) events.push({ event, data: dataLines.join('\n') })
  }
  return { events, rest }
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { error?: { message?: string } }
    if (payload.error?.message) return payload.error.message
  } catch {
    // Not a JSON error body; fall through to the status text.
  }
  return `请求失败（${response.status}），请稍后重试`
}

/**
 * POST /chat/stream and dispatch the answer as it is written.
 * Rejects with an Error whose message is safe to show to the user.
 */
export async function streamChat(
  body: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
  const csrfToken = getCsrfToken()
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken
  const response = await fetch(apiUrl('/chat/stream'), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    credentials: 'include',
    signal,
  })
  if (response.status === 401) {
    clearStoredAuth()
    if (window.location.pathname !== '/login') {
      const redirect = `${window.location.pathname}${window.location.search}`
      window.location.assign(`/login?redirect=${encodeURIComponent(redirect)}`)
    }
    throw new Error('登录状态已失效，请重新登录')
  }
  if (!response.ok || !response.body) throw new Error(await responseErrorMessage(response))

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finished = false
  const dispatch = (item: SseEvent) => {
    const data = JSON.parse(item.data) as Record<string, unknown>
    if (item.event === 'meta') handlers.onMeta?.(data as { model?: string })
    else if (item.event === 'reasoning') handlers.onReasoning?.(String(data.text ?? ''))
    else if (item.event === 'delta') handlers.onDelta?.(String(data.text ?? ''))
    else if (item.event === 'done') {
      finished = true
      handlers.onDone?.(data as ChatStreamDone)
    } else if (item.event === 'error') {
      throw new Error(String(data.message || '模型服务暂时不可用，请稍后重试'))
    }
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseSseBuffer(buffer)
    buffer = parsed.rest
    parsed.events.forEach(dispatch)
  }
  if (!finished) throw new Error('回答连接中断，请重试')
}
