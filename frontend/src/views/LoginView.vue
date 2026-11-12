<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  PencilLine,
  Phone,
  ShieldCheck,
  UserRound,
} from 'lucide-vue-next'

import { loginWithPassword, loginWithSms, registerWithPassword, requestSmsCode } from '@/auth/session'

type LoginMode = 'password' | 'sms'
type AccessMode = 'login' | 'register'

const route = useRoute()
const router = useRouter()
const accessMode = ref<AccessMode>('login')
const mode = ref<LoginMode>('password')
const phone = ref(localStorage.getItem('zhiyan.login.phone') ?? '')
const password = ref('')
const code = ref('')
const registerCode = ref('')
const registerName = ref('')
const registerOrganization = ref('')
const confirmPassword = ref('')
const rememberPhone = ref(Boolean(phone.value))
const showPassword = ref(false)
const showRegisterPassword = ref(false)
const showRegisterConfirmPassword = ref(false)
const busy = ref(false)
const sendingCode = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const canSubmit = computed(() => {
  if (!phone.value.trim() || busy.value) return false
  if (accessMode.value === 'register') {
    return Boolean(registerName.value.trim() && password.value && confirmPassword.value && registerCode.value.trim())
  }
  return mode.value === 'password' ? Boolean(password.value) : Boolean(code.value.trim())
})

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    if (accessMode.value === 'register') {
      if (password.value !== confirmPassword.value) {
        errorMessage.value = '两次输入的密码不一致'
        return
      }
      await registerWithPassword({
        phone: phone.value,
        password: password.value,
        name: registerName.value,
        organization: registerOrganization.value || undefined,
        code: registerCode.value,
      })
      accessMode.value = 'login'
      mode.value = 'password'
      registerName.value = ''
      password.value = ''
      code.value = ''
      registerCode.value = ''
      confirmPassword.value = ''
      registerOrganization.value = ''
      showRegisterPassword.value = false
      showRegisterConfirmPassword.value = false
      localStorage.setItem('zhiyan.login.phone', phone.value.trim())
      rememberPhone.value = true
      successMessage.value = '注册成功，请使用新账号登录'
      return
    }
    if (mode.value === 'password') await loginWithPassword(phone.value, password.value)
    else await loginWithSms(phone.value, code.value)

    if (rememberPhone.value) localStorage.setItem('zhiyan.login.phone', phone.value.trim())
    else localStorage.removeItem('zhiyan.login.phone')
    await router.replace(safeRedirect(route.query.redirect))
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    busy.value = false
  }
}

async function sendCode() {
  if (!phone.value.trim() || sendingCode.value) return
  sendingCode.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await requestSmsCode(phone.value, accessMode.value === 'register' ? 'register' : 'login')
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    sendingCode.value = false
  }
}

function switchMode(nextMode: LoginMode) {
  mode.value = nextMode
  errorMessage.value = ''
  successMessage.value = ''
}

function switchAccessMode(nextMode: AccessMode) {
  accessMode.value = nextMode
  mode.value = 'password'
  code.value = ''
  errorMessage.value = ''
  successMessage.value = ''
}

function safeRedirect(value: unknown) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '登录失败，请稍后重试'
}
</script>

<template>
  <main class="login-page">
    <section class="login-brand-panel" aria-label="智研科研工作台">
      <div class="login-brand">
        <span class="login-brand-mark"><BookOpen :size="24" /></span>
        <span><strong>智研</strong><small>ZHICY RESEARCH</small></span>
      </div>

      <div class="login-brand-copy">
        <p>RESEARCH WORKSPACE</p>
        <h1>让每一次研究探索<br />都有迹可循</h1>
        <div class="login-capabilities">
          <span><CheckCircle2 :size="16" />连续保存研究任务与结果</span>
          <span><ShieldCheck :size="16" />个人数据与权限独立隔离</span>
          <span><KeyRound :size="16" />统一管理模型与知识资源</span>
        </div>
      </div>

      <div class="login-security-note"><ShieldCheck :size="16" />安全连接 · 账号数据独立存储</div>
    </section>

    <section class="login-form-panel">
      <form class="login-form" @submit.prevent="submit">
        <header>
          <p>ACCOUNT ACCESS</p>
          <h2>{{ accessMode === 'login' ? '登录智研' : '注册账号' }}</h2>
          <span>{{ accessMode === 'login' ? '进入你的科研工作台' : '创建账号后返回登录界面' }}</span>
        </header>

        <div class="login-access-switch" role="tablist" aria-label="账号访问方式">
          <button type="button" role="tab" :aria-selected="accessMode === 'login'" :class="{ active: accessMode === 'login' }" @click="switchAccessMode('login')">
            <LockKeyhole :size="16" />登录
          </button>
          <button type="button" role="tab" :aria-selected="accessMode === 'register'" :class="{ active: accessMode === 'register' }" @click="switchAccessMode('register')">
            <PencilLine :size="16" />注册
          </button>
        </div>

        <div v-if="accessMode === 'login'" class="login-mode-switch" role="tablist" aria-label="登录方式">
          <button type="button" role="tab" :aria-selected="mode === 'password'" :class="{ active: mode === 'password' }" @click="switchMode('password')">
            <LockKeyhole :size="16" />密码登录
          </button>
          <button type="button" role="tab" :aria-selected="mode === 'sms'" :class="{ active: mode === 'sms' }" @click="switchMode('sms')">
            <MessageSquareText :size="16" />验证码登录
          </button>
        </div>

        <label v-if="accessMode === 'register'" class="login-field">
          <span>昵称 / 姓名</span>
          <span class="login-input-wrap">
            <UserRound :size="17" />
            <input v-model="registerName" autocomplete="nickname" placeholder="请输入昵称或姓名" />
          </span>
        </label>

        <label class="login-field">
          <span>手机号</span>
          <span class="login-input-wrap">
            <Phone :size="17" />
            <input v-model="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="请输入手机号" />
          </span>
        </label>

        <label v-if="accessMode === 'register'" class="login-field">
          <span>短信验证码</span>
          <span class="login-input-wrap login-code-input">
            <MessageSquareText :size="17" />
            <input v-model="registerCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="请先获取验证码" />
            <button type="button" :disabled="!phone.trim() || sendingCode" @click="sendCode">
              {{ sendingCode ? '发送中' : '获取验证码' }}
            </button>
          </span>
        </label>

        <label v-if="accessMode === 'register'" class="login-field">
          <span>机构（可选）</span>
          <span class="login-input-wrap">
            <BookOpen :size="17" />
            <input v-model="registerOrganization" autocomplete="organization" placeholder="请输入机构名称" />
          </span>
        </label>

        <label v-if="accessMode === 'register' || mode === 'password'" class="login-field">
          <span>密码</span>
          <span class="login-input-wrap">
            <LockKeyhole :size="17" />
            <input
              v-model="password"
              :type="accessMode === 'register' ? (showRegisterPassword ? 'text' : 'password') : (showPassword ? 'text' : 'password')"
              :autocomplete="accessMode === 'register' ? 'new-password' : 'current-password'"
              :placeholder="accessMode === 'register' ? '请设置至少 8 位密码' : '请输入密码'"
            />
            <button
              type="button"
              :aria-label="accessMode === 'register'
                ? (showRegisterPassword ? '隐藏密码' : '显示密码')
                : (showPassword ? '隐藏密码' : '显示密码')"
              :title="accessMode === 'register'
                ? (showRegisterPassword ? '隐藏密码' : '显示密码')
                : (showPassword ? '隐藏密码' : '显示密码')"
              @click="accessMode === 'register' ? (showRegisterPassword = !showRegisterPassword) : (showPassword = !showPassword)"
            >
              <EyeOff v-if="accessMode === 'register' ? showRegisterPassword : showPassword" :size="17" /><Eye v-else :size="17" />
            </button>
          </span>
        </label>

        <label v-if="accessMode === 'register'" class="login-field">
          <span>确认密码</span>
          <span class="login-input-wrap">
            <LockKeyhole :size="17" />
            <input v-model="confirmPassword" :type="showRegisterConfirmPassword ? 'text' : 'password'" autocomplete="new-password" placeholder="请再次输入密码" />
            <button type="button" :aria-label="showRegisterConfirmPassword ? '隐藏密码' : '显示密码'" :title="showRegisterConfirmPassword ? '隐藏密码' : '显示密码'" @click="showRegisterConfirmPassword = !showRegisterConfirmPassword">
              <EyeOff v-if="showRegisterConfirmPassword" :size="17" /><Eye v-else :size="17" />
            </button>
          </span>
        </label>

        <label v-else-if="mode === 'sms'" class="login-field">
          <span>验证码</span>
          <span class="login-input-wrap login-code-input">
            <MessageSquareText :size="17" />
            <input v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="请输入验证码" />
            <button type="button" :disabled="!phone.trim() || sendingCode" @click="sendCode">
              {{ sendingCode ? '发送中' : '获取验证码' }}
            </button>
          </span>
        </label>

        <div v-if="accessMode === 'login'" class="login-options">
          <label><input v-model="rememberPhone" type="checkbox" />记住手机号</label>
        </div>

        <p v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</p>
        <p v-if="successMessage" class="login-success" role="status">{{ successMessage }}</p>

        <button class="login-submit" type="submit" :disabled="!canSubmit">
          <LoaderCircle v-if="busy" class="spin" :size="18" />
          <template v-else>{{ accessMode === 'login' ? '登录' : '完成注册' }}<ArrowRight :size="18" /></template>
        </button>
      </form>
    </section>
  </main>
</template>
