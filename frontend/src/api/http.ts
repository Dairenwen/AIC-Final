import axios from 'axios'

import { clearStoredAuth, getCsrfToken } from '@/auth/storage'

export interface ApiEnvelope<T> {
  success: boolean
  data: T
  meta?: Record<string, unknown>
}

export const http = axios.create({
  baseURL: normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL || '/api/v1'),
  timeout: 15000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

/** Prevent deployment envs/proxies from duplicating the API prefix. */
export function normalizeApiBaseUrl(value: string): string {
  const trimmed = String(value || '/api/v1').trim().replace(/\/+$/, '') || '/api/v1'
  return trimmed.replace(/(?:\/api\/v1)+$/i, '/api/v1')
}

export function apiUrl(path: string): string {
  const base = normalizeApiBaseUrl(http.defaults.baseURL || '/api/v1')
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${base}${suffix}`
}

http.interceptors.request.use((config) => {
  const csrfToken = getCsrfToken()
  if (csrfToken && config.method && ['post', 'put', 'patch', 'delete'].includes(config.method)) {
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  if (config.method === 'post' && isTaskCreateUrl(config.url) && config.data && typeof config.data === 'object' && !config.data.project_id) {
    const projectId = projectContextFromSearch(window.location.search)
    if (projectId) config.data = { ...config.data, project_id: projectId }
  }
  // Some production nginx configurations canonicalize `/tasks` to
  // `/tasks/` with a 301.  Send the canonical form up front so the POST is
  // never converted into a history GET by the browser.
  if (config.method === 'post' && isTaskCreateUrl(config.url)) config.url = '/tasks/'
  return config
})

http.interceptors.response.use(
  (response) => {
    // Normalize task-create responses once for every Agent view.  Depending
    // on the reverse proxy/API adapter, the task can be wrapped in several
    // `data`/`task`/`result` layers; legacy keys are handled as well.
    if (response.config.method === 'post' && isTaskCreateUrl(response.config.url)) {
      const task = taskRecordFromResponse(response.data)
      if (!Object.keys(task).length) {
        throw new Error('任务创建接口返回了列表或无效数据，请检查服务端尾斜杠重定向和 API 版本')
      }
      response.data = { ...(response.data || {}), data: task }
    }
    return response
  },
  (error) => {
    if (error.response?.status === 401) {
      clearStoredAuth()
      const requestUrl = String(error.config?.url ?? '')
      if (window.location.pathname !== '/login' && requestUrl !== '/auth/login') {
        const redirect = `${window.location.pathname}${window.location.search}`
        window.location.assign(`/login?redirect=${encodeURIComponent(redirect)}`)
      }
    }
    return Promise.reject(error)
  },
)

export async function getData<T>(url: string, config = {}): Promise<T> {
  const response = await http.get<ApiEnvelope<T>>(url, config)
  return response.data.data
}

export function projectContextFromSearch(search: string): string | null {
  const value = new URLSearchParams(search).get('project')?.trim()
  return value || null
}

function isTaskCreateUrl(url: string | undefined): boolean {
  return url === '/tasks' || url === '/tasks/'
}

/**
 * Extract a task identifier from both the current API envelope and responses
 * wrapped by a reverse proxy/legacy adapter.  Some deployments return one or
 * more nested `data`/`task` objects, while older clients use task_id/taskId.
 */
export function taskIdFromResponse(value: unknown): string | null {
  let current: unknown = value
  for (let depth = 0; depth < 5; depth += 1) {
    // A task-create endpoint must never accept a list.  Following the first
    // item turns a 301/POST -> GET response into an unrelated historical task.
    if (Array.isArray(current)) return null
    if (!current || typeof current !== 'object') return null
    const record = current as Record<string, unknown>
    for (const key of ['id', 'task_id', 'taskId', 'task_uuid', 'taskUuid', 'uuid']) {
      const candidate = record[key]
      if (candidate != null && String(candidate).trim()) return String(candidate)
    }
    current = record.data ?? record.task ?? record.result
  }
  return null
}

export function taskRecordFromResponse(value: unknown): Record<string, unknown> {
  let current: unknown = value
  for (let depth = 0; depth < 5; depth += 1) {
    if (Array.isArray(current)) return {}
    if (!current || typeof current !== 'object') return {}
    const record = current as Record<string, unknown>
    if (['id', 'task_id', 'taskId', 'task_uuid', 'taskUuid', 'uuid'].some((key) => {
      const candidate = record[key]
      return candidate != null && String(candidate).trim()
    })) return record
    current = record.data ?? record.task ?? record.result
  }
  return {}
}
