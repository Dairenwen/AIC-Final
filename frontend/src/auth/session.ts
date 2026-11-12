import { reactive } from 'vue'

import { http } from '@/api/http'
import { clearStoredAuth, readStoredUser, storeAuth } from './storage'

export interface AuthUser {
  id: string
  phone: string
  name: string
  organization: string
  role: string
  plan: string
}

interface AuthPayload {
  user: AuthUser
  csrfToken: string
}

export const authState = reactive({
  user: readStoredUser<AuthUser>(),
  initialized: false,
})

export async function restoreSession() {
  if (authState.initialized) return Boolean(authState.user)
  try {
    const response = await http.get<{ data: AuthPayload }>('/auth/me')
    setSession(response.data.data)
    return true
  } catch {
    clearSession()
    return false
  } finally {
    authState.initialized = true
  }
}

export async function loginWithPassword(phone: string, password: string) {
  const response = await http.post<{ data: AuthPayload }>('/auth/login', { phone, password })
  setSession(response.data.data)
  authState.initialized = true
  return authState.user
}

export async function registerWithPassword(payload: {
  phone: string
  password: string
  name: string
  organization?: string
  code: string
}) {
  const response = await http.post('/auth/register', payload)
  return response.data.data as { user: AuthUser; message: string }
}

export async function requestSmsCode(phone: string, purpose: 'login' | 'register' = 'login') {
  await http.post('/auth/sms/request', { phone, purpose })
}

export async function loginWithSms(phone: string, code: string) {
  const response = await http.post<{ data: AuthPayload }>('/auth/sms/login', { phone, code })
  setSession(response.data.data)
  authState.initialized = true
  return authState.user
}

export async function logout() {
  try {
    await http.post('/auth/logout')
  } finally {
    clearSession()
    authState.initialized = true
  }
}

export function clearSession() {
  authState.user = null
  clearStoredAuth()
}

function setSession(payload: AuthPayload) {
  authState.user = payload.user
  storeAuth(payload.user, payload.csrfToken)
}
