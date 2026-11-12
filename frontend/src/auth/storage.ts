const USER_KEY = 'zhiyan.auth.user'
const CSRF_KEY = 'zhiyan.auth.csrf'

export function readStoredUser<T>(): T | null {
  const value = sessionStorage.getItem(USER_KEY)
  if (!value) return null
  try {
    return JSON.parse(value) as T
  } catch {
    clearStoredAuth()
    return null
  }
}

export function storeAuth(user: unknown, csrfToken: string) {
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
  sessionStorage.setItem(CSRF_KEY, csrfToken)
}

export function getCsrfToken() {
  return sessionStorage.getItem(CSRF_KEY) ?? ''
}

export function clearStoredAuth() {
  sessionStorage.removeItem(USER_KEY)
  sessionStorage.removeItem(CSRF_KEY)
}
