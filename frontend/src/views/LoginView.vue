<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LoaderCircle } from 'lucide-vue-next'

import {
  fetchSignInOptions,
  loginWithPassword,
  loginWithSms,
  registerWithPassword,
  requestSmsCode,
} from '@/auth/session'
import BrandMark from '@/components/icons/BrandMark.vue'
import ZyIcon from '@/components/icons/ZyIcon.vue'

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
// Text-message codes are offered only when the server can send them.
const smsEnabled = ref(false)

onMounted(async () => {
  smsEnabled.value = (await fetchSignInOptions()).smsEnabled
})

const canSubmit = computed(() => {
  if (!phone.value.trim() || busy.value) return false
  if (accessMode.value === 'register') {
    const codeReady = !smsEnabled.value || Boolean(registerCode.value.trim())
    return Boolean(registerName.value.trim() && password.value && confirmPassword.value && codeReady)
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
      successMessage.value = '账号已创建，请登录'
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
    successMessage.value = '验证码已发送'
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
  return value.response?.data?.error?.message || value.message || '没有成功，请稍后再试'
}
</script>

<template>
  <main class="login-page">
    <div class="login-sheet">
      <header class="login-head">
        <BrandMark class="brand-glyph--draw" :size="48" />
        <h1>智研</h1>
        <p>{{ accessMode === 'login' ? '登录后继续你的研究' : '创建一个新账号' }}</p>
      </header>

      <form class="login-form" @submit.prevent="submit">
        <div class="login-switch" role="tablist" aria-label="登录或注册" :data-active="accessMode">
          <button type="button" role="tab" :aria-selected="accessMode === 'login'" @click="switchAccessMode('login')">登录</button>
          <button type="button" role="tab" :aria-selected="accessMode === 'register'" @click="switchAccessMode('register')">注册</button>
        </div>

        <Transition name="swap" mode="out-in">
          <div :key="`${accessMode}-${mode}`" class="login-fields">
            <label v-if="accessMode === 'register'" class="login-field">
              <span>姓名</span>
              <span class="login-input-wrap">
                <ZyIcon name="user" :size="18" />
                <input v-model="registerName" autocomplete="nickname" placeholder="怎么称呼你" />
              </span>
            </label>

            <label class="login-field">
              <span>手机号</span>
              <span class="login-input-wrap">
                <ZyIcon name="phone" :size="18" />
                <input v-model="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="11 位手机号" />
              </span>
            </label>

            <label v-if="accessMode === 'register' && smsEnabled" class="login-field">
              <span>短信验证码</span>
              <span class="login-input-wrap login-code-input">
                <ZyIcon name="message" :size="18" />
                <input v-model="registerCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6 位验证码" />
                <button type="button" :disabled="!phone.trim() || sendingCode" @click="sendCode">
                  {{ sendingCode ? '发送中' : '获取验证码' }}
                </button>
              </span>
            </label>

            <label v-if="accessMode === 'register'" class="login-field">
              <span>学校或机构<em>选填</em></span>
              <span class="login-input-wrap">
                <ZyIcon name="building" :size="18" />
                <input v-model="registerOrganization" autocomplete="organization" placeholder="例如：武汉理工大学" />
              </span>
            </label>

            <label v-if="accessMode === 'register' || mode === 'password'" class="login-field">
              <span>密码</span>
              <span class="login-input-wrap">
                <ZyIcon name="lock" :size="18" />
                <input
                  v-model="password"
                  :type="accessMode === 'register' ? (showRegisterPassword ? 'text' : 'password') : (showPassword ? 'text' : 'password')"
                  :autocomplete="accessMode === 'register' ? 'new-password' : 'current-password'"
                  :placeholder="accessMode === 'register' ? '至少 8 位' : '输入密码'"
                />
                <button
                  type="button"
                  :aria-label="(accessMode === 'register' ? showRegisterPassword : showPassword) ? '隐藏密码' : '显示密码'"
                  :title="(accessMode === 'register' ? showRegisterPassword : showPassword) ? '隐藏密码' : '显示密码'"
                  @click="accessMode === 'register' ? (showRegisterPassword = !showRegisterPassword) : (showPassword = !showPassword)"
                >
                  <ZyIcon :name="(accessMode === 'register' ? showRegisterPassword : showPassword) ? 'eyeOff' : 'eye'" :size="18" />
                </button>
              </span>
            </label>

            <label v-if="accessMode === 'register'" class="login-field">
              <span>确认密码</span>
              <span class="login-input-wrap">
                <ZyIcon name="lock" :size="18" />
                <input v-model="confirmPassword" :type="showRegisterConfirmPassword ? 'text' : 'password'" autocomplete="new-password" placeholder="再输入一次" />
                <button type="button" :aria-label="showRegisterConfirmPassword ? '隐藏密码' : '显示密码'" :title="showRegisterConfirmPassword ? '隐藏密码' : '显示密码'" @click="showRegisterConfirmPassword = !showRegisterConfirmPassword">
                  <ZyIcon :name="showRegisterConfirmPassword ? 'eyeOff' : 'eye'" :size="18" />
                </button>
              </span>
            </label>

            <label v-else-if="mode === 'sms'" class="login-field">
              <span>验证码</span>
              <span class="login-input-wrap login-code-input">
                <ZyIcon name="message" :size="18" />
                <input v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6 位验证码" />
                <button type="button" :disabled="!phone.trim() || sendingCode" @click="sendCode">
                  {{ sendingCode ? '发送中' : '获取验证码' }}
                </button>
              </span>
            </label>
          </div>
        </Transition>

        <div v-if="accessMode === 'login'" class="login-options">
          <label><input v-model="rememberPhone" type="checkbox" />记住手机号</label>
          <button v-if="smsEnabled" type="button" class="login-link" @click="switchMode(mode === 'password' ? 'sms' : 'password')">
            {{ mode === 'password' ? '用验证码登录' : '用密码登录' }}
          </button>
        </div>

        <p v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</p>
        <p v-if="successMessage" class="login-success" role="status">{{ successMessage }}</p>

        <button class="login-submit nudge-arrow" type="submit" :disabled="!canSubmit">
          <LoaderCircle v-if="busy" class="spin" :size="18" />
          <template v-else>{{ accessMode === 'login' ? '登录' : '注册' }}<ZyIcon name="arrowRight" :size="18" /></template>
        </button>
      </form>
    </div>
  </main>
</template>
