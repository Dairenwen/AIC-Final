<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertCircle, Check, CircleCheck, Cpu, LoaderCircle, Pencil, Play, Plus, Power, Save, Server, Star, Trash2, X } from 'lucide-vue-next'

import { getData, http } from '@/api/http'
import type { DefaultModelConfig, ModelConfig, ModelProvider, ModelType } from '@/types'

withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })

const providers = ref<ModelProvider[]>([])
const modelTypes = ref<ModelType[]>([])
const models = ref<ModelConfig[]>([])
const activeType = ref('chat')
const defaultModel = ref<DefaultModelConfig>({ value: 'api_model', name: 'API 对话模型', model_name: 'deepseek-ai/DeepSeek-V4-Flash', source: 'account_api', config_id: null })
const loading = ref(true)
const actionId = ref('')
const saving = ref(false)
const showEditor = ref(false)
const editingId = ref<string | null>(null)
const errorMessage = ref('')
const notice = ref('')

const emptyForm = () => ({ provider_code: 'openai_compatible', model_type_code: 'chat', name: '', base_url: '', model_name: '', api_key: '', timeout_seconds: 120, max_output_tokens: 3072 })
const form = reactive(emptyForm())
const customProvider = reactive({ name: '', default_base_url: '' })
const customModelType = reactive({ name: '', description: '' })
const formTitle = computed(() => editingId.value ? '编辑模型' : '添加模型')
const providerName = (code: string) => providers.value.find((item) => item.code === code)?.name || code
const modelTypeName = (code: string) => modelTypes.value.find((item) => item.code === code)?.name || code
// Legacy personal model records remain available to the backend. The system
// library exposes the two platform-managed system services below.
const visibleModels = computed<ModelConfig[]>(() => [])
const apiModel = computed(() => models.value.find((item) => item.is_account_api) || null)
const statusText: Record<string, string> = { DRAFT: '待测试', VERIFYING: '测试中', ACTIVE: '可用', INVALID: '连接失败', DISABLED: '已停用' }

async function loadModels() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [providerData, typeData, modelData, defaultData] = await Promise.all([
      getData<ModelProvider[]>('/model-providers'),
      getData<ModelType[]>('/model-types'),
      getData<ModelConfig[]>('/model-configs'),
      getData<DefaultModelConfig>('/model-configs/default'),
    ])
    providers.value = providerData
    modelTypes.value = typeData
    if (!typeData.some((item) => item.code === activeType.value)) {
      activeType.value = typeData[0]?.code || 'chat'
    }
    models.value = modelData
    defaultModel.value = defaultData
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    loading.value = false
  }
}

function selectProvider() {
  if (form.provider_code === '__custom__') return
  const provider = providers.value.find((item) => item.code === form.provider_code)
  if (provider?.default_base_url) form.base_url = provider.default_base_url
}

function openCreate() {
  editingId.value = null
  Object.assign(form, emptyForm())
  Object.assign(customProvider, { name: '', default_base_url: '' })
  Object.assign(customModelType, { name: '', description: '' })
  showEditor.value = true
  errorMessage.value = ''
  notice.value = ''
}

function editModel(item: ModelConfig) {
  editingId.value = item.id
  Object.assign(form, { provider_code: item.provider_code, model_type_code: item.model_type_code, name: item.name, base_url: item.base_url, model_name: item.model_name, api_key: '', timeout_seconds: Number(item.settings.timeout_seconds || 120), max_output_tokens: Number(item.settings.max_output_tokens || 3072) })
  showEditor.value = true
  errorMessage.value = ''
  notice.value = ''
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function closeEditor() {
  showEditor.value = false
  editingId.value = null
  Object.assign(form, emptyForm())
  Object.assign(customProvider, { name: '', default_base_url: '' })
  Object.assign(customModelType, { name: '', description: '' })
}

async function saveModel() {
  if (!form.name.trim() || !form.base_url.trim() || !form.model_name.trim()) {
    errorMessage.value = '请完整填写显示名称、接口地址和模型名称'
    return
  }
  if (!editingId.value && !form.api_key.trim()) {
    errorMessage.value = '请输入 API Key'
    return
  }
  saving.value = true
  errorMessage.value = ''
  try {
    let providerCode = form.provider_code
    if (providerCode === '__custom__') {
      if (!customProvider.name.trim() || !customProvider.default_base_url.trim()) {
        errorMessage.value = '请填写自定义提供商名称和默认接口地址'
        return
      }
      const created = await http.post<{ data: ModelProvider }>('/model-providers', { name: customProvider.name.trim(), default_base_url: customProvider.default_base_url.trim() })
      providerCode = created.data.data.code
    }
    let modelTypeCode = form.model_type_code
    if (modelTypeCode === '__custom__') {
      if (!customModelType.name.trim()) {
        errorMessage.value = '请填写自定义模型类型名称'
        return
      }
      const created = await http.post<{ data: ModelType }>('/model-types', { name: customModelType.name.trim(), description: customModelType.description.trim() })
      modelTypeCode = created.data.data.code
    }
    const payload = { provider_code: providerCode, model_type_code: modelTypeCode, name: form.name.trim(), base_url: form.base_url.trim(), model_name: form.model_name.trim(), api_key: form.api_key.trim() || undefined, timeout_seconds: form.timeout_seconds, max_output_tokens: form.max_output_tokens }
    if (editingId.value) await http.patch('/model-configs/' + editingId.value, payload)
    else await http.post('/model-configs', payload)
    notice.value = editingId.value ? '模型配置已更新，请重新测试连接' : '模型已添加，请测试连接'
    activeType.value = modelTypeCode
    closeEditor()
    await loadModels()
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    saving.value = false
  }
}

async function verifyModel(item?: ModelConfig, service: 'api' | 'platform' = 'platform') {
  const id = item?.id || service
  actionId.value = id
  errorMessage.value = ''
  notice.value = ''
  try {
    // Model verification can wait behind other generations on a shared GPU.
    // Keep the normal API timeout for ordinary requests, but allow this
    // explicit probe to match the backend's 60-second verification budget.
    await http.post(
      item ? '/model-configs/' + item.id + '/verify' : service === 'api' ? '/model-configs/api/verify' : '/model-configs/vertical/verify',
      undefined,
      { timeout: 65_000 },
    )
    notice.value = '“' + (item?.name || (service === 'api' ? 'API 对话模型' : '平台专用模型')) + '”连接测试通过'
    await loadModels()
  } catch (error) {
    const failureReason = requestError(error)
    await loadModels()
    errorMessage.value = '“' + (item?.name || (service === 'api' ? 'API 对话模型' : '平台专用模型')) + '”连接测试失败：' + failureReason
  } finally {
    actionId.value = ''
  }
}

async function setDefault(item?: ModelConfig, target: 'api_model' | 'vertical_domain' = 'vertical_domain') {
  actionId.value = item?.id || target
  errorMessage.value = ''
  try {
    const response = await http.post<{ data: DefaultModelConfig }>('/model-configs/default', { config_id: item?.id || target })
    defaultModel.value = response.data.data
    notice.value = '默认对话模型已切换为“' + (item?.name || '平台通用模型') + '”'
    await loadModels()
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionId.value = ''
  }
}

async function toggleModel(item: ModelConfig) {
  actionId.value = item.id
  errorMessage.value = ''
  try {
    await http.post('/model-configs/' + item.id + '/status', { status: item.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' })
    await loadModels()
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionId.value = ''
  }
}

async function deleteModel(item: ModelConfig) {
  if (!window.confirm('确认删除模型“' + item.name + '”？')) return
  actionId.value = item.id
  try {
    await http.delete('/model-configs/' + item.id)
    notice.value = '模型已删除'
    await loadModels()
  } catch (error) {
    errorMessage.value = requestError(error)
  } finally {
    actionId.value = ''
  }
}

function requestError(error: unknown) {
  const value = error as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return value.response?.data?.error?.message || value.message || '请求失败，请稍后重试'
}

onMounted(loadModels)
</script>

<template>
  <div :class="['model-library-page', { 'workspace-page': !embedded, 'model-library-page--embedded': embedded }]">
    <header class="model-library-header">
      <div><h1>模型库</h1><p>默认使用 API 对话模型，也可切换平台专用模型。</p></div>
      <button v-if="false" class="primary-button" type="button" @click="openCreate"><Plus :size="16" />添加模型</button>
    </header>

    <nav v-if="false" class="model-library-tabs" aria-label="模型类型">
      <button v-for="item in modelTypes" :key="item.code" :class="{ active: activeType === item.code }" type="button" @click="activeType = item.code">{{ item.name }}</button>
    </nav>

    <p v-if="errorMessage" class="profile-alert profile-alert--error" role="alert"><AlertCircle :size="15" />{{ errorMessage }}</p>
    <p v-if="notice" class="profile-alert profile-alert--success"><Check :size="14" />{{ notice }}</p>

    <div v-if="false && showEditor" class="model-library-modal" role="dialog" aria-modal="true" aria-label="模型编辑器">
      <button class="model-library-modal__backdrop" type="button" aria-label="关闭模型编辑器" @click="closeEditor"></button>
      <section class="model-library-editor">
        <div class="model-section-heading">
          <div><h2>{{ formTitle }}</h2><p>{{ editingId ? 'API Key 留空时保留原密钥。' : '配置模型类型、提供商与连接参数。' }}</p></div>
          <button class="icon-button" type="button" aria-label="关闭编辑器" title="关闭编辑器" @click="closeEditor"><X :size="16" /></button>
        </div>
        <div class="form-grid">
          <label><span>模型类型</span><select v-model="form.model_type_code"><option v-for="item in modelTypes" :key="item.code" :value="item.code">{{ item.name }}</option><option value="__custom__">+ 自定义模型类型</option></select></label>
          <label><span>提供商</span><select v-model="form.provider_code" @change="selectProvider"><option v-for="item in providers" :key="item.code" :value="item.code">{{ item.name }}</option><option value="__custom__">+ 自定义提供商</option></select></label>
          <label v-if="form.model_type_code === '__custom__'" class="form-span"><span>自定义模型类型名称</span><input v-model="customModelType.name" maxlength="120" placeholder="例如：多模态模型" /></label>
          <label v-if="form.model_type_code === '__custom__'"><span>类型说明</span><input v-model="customModelType.description" placeholder="可选" /></label>
          <label v-if="form.provider_code === '__custom__'"><span>自定义提供商名称</span><input v-model="customProvider.name" maxlength="120" placeholder="例如：本地推理服务" /></label>
          <label v-if="form.provider_code === '__custom__'"><span>提供商默认地址</span><input v-model="customProvider.default_base_url" type="url" placeholder="https://api.example.com/v1" /></label>
          <label><span>显示名称</span><input v-model="form.name" maxlength="120" placeholder="例如：实验室 Qwen" /></label>
          <label><span>模型名称</span><input v-model="form.model_name" placeholder="例如：qwen-plus" /></label>
          <label><span>API Key</span><input v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="editingId ? '留空以保留原密钥' : '输入 API Key'" /></label>
          <label class="form-span"><span>API Base URL</span><input v-model="form.base_url" type="url" placeholder="https://api.example.com/v1" /></label>
          <label><span>请求超时（秒）</span><input v-model.number="form.timeout_seconds" type="number" min="10" max="600" /></label>
          <label><span>最大输出 Token</span><input v-model.number="form.max_output_tokens" type="number" min="256" max="16384" step="256" /></label>
        </div>
        <div class="form-actions model-editor-actions">
          <button class="secondary-button" type="button" @click="closeEditor">取消</button>
          <button class="primary-button" type="button" :disabled="saving" @click="saveModel"><LoaderCircle v-if="saving" class="spin" :size="15" /><Save v-else :size="15" />保存模型</button>
        </div>
      </section>
    </div>

    <div v-if="loading" class="model-empty"><LoaderCircle class="spin" :size="22" /><span>正在加载模型库</span></div>
    <section v-else class="model-card-grid" :aria-label="modelTypeName(activeType) + '列表'">
      <article v-if="activeType === 'chat'" class="model-library-card model-library-card--builtin model-library-card--api">
        <div class="model-card-heading">
          <span class="model-card-icon"><Server :size="18" /></span>
          <div><h2>API 对话模型</h2><span>API 模型服务</span></div>
          <span v-if="defaultModel.value === 'api_model'" class="model-default-badge"><Star :size="11" />默认</span>
        </div>
        <dl>
          <div><dt>模型</dt><dd>{{ apiModel?.model_name || defaultModel.model_name }}</dd></div>
          <div><dt>接口</dt><dd :title="apiModel?.base_url">{{ apiModel?.base_url || 'API 模型服务' }}</dd></div>
          <div><dt>密钥</dt><dd><CircleCheck :size="13" />平台托管</dd></div>
        </dl>
        <div class="model-card-actions">
          <button type="button" :disabled="actionId === 'api'" @click="verifyModel(undefined, 'api')"><LoaderCircle v-if="actionId === 'api'" class="spin" :size="14" /><Play v-else :size="14" />测试</button>
          <button v-if="defaultModel.value !== 'api_model'" type="button" @click="setDefault(undefined, 'api_model')"><Star :size="14" />设为默认</button>
          <button v-else type="button" disabled><Star :size="14" />当前默认</button>
        </div>
      </article>

      <article v-if="activeType === 'chat'" class="model-library-card model-library-card--builtin">
        <div class="model-card-heading">
          <span class="model-card-icon"><Cpu :size="18" /></span>
          <div><h2>平台专用模型</h2><span>平台模型服务</span></div>
          <span v-if="defaultModel.value === 'vertical_domain'" class="model-default-badge"><Star :size="11" />默认</span>
        </div>
        <dl>
          <div><dt>模型</dt><dd>qwen3.6-27B-sft</dd></div>
          <div><dt>接口</dt><dd>平台模型服务</dd></div>
          <div><dt>密钥</dt><dd><CircleCheck :size="13" />平台托管</dd></div>
        </dl>
        <div class="model-card-actions">
          <button type="button" :disabled="actionId === 'platform'" @click="verifyModel(undefined, 'platform')"><LoaderCircle v-if="actionId === 'platform'" class="spin" :size="14" /><Play v-else :size="14" />测试</button>
          <button v-if="defaultModel.value !== 'vertical_domain'" type="button" @click="setDefault(undefined, 'vertical_domain')"><Star :size="14" />设为默认</button>
          <button v-else type="button" disabled><Star :size="14" />当前默认</button>
        </div>
      </article>

      <article v-for="item in visibleModels" :key="item.id" class="model-library-card">
        <div class="model-card-heading">
          <span class="model-card-icon"><Server :size="18" /></span>
          <div><h2>{{ item.name }}</h2><span>{{ item.is_account_api ? '账号基础配置' : providerName(item.provider_code) }}</span></div>
          <span v-if="item.is_default" class="model-default-badge"><Star :size="11" />默认</span>
          <span v-else class="model-status" :class="'model-status--' + item.status.toLowerCase()">{{ statusText[item.status] || item.status }}</span>
        </div>
        <dl>
          <div><dt>模型</dt><dd>{{ item.model_name }}</dd></div>
          <div><dt>接口</dt><dd :title="item.base_url">{{ item.base_url }}</dd></div>
          <div><dt>密钥</dt><dd><CircleCheck v-if="item.has_api_key" :size="13" />{{ item.masked_api_key || '未配置' }}</dd></div>
        </dl>
        <div class="model-card-actions model-card-actions--personal">
          <button type="button" :disabled="actionId === item.id" @click="verifyModel(item)"><LoaderCircle v-if="actionId === item.id" class="spin" :size="14" /><Play v-else :size="14" />测试</button>
          <button type="button" @click="editModel(item)"><Pencil :size="14" />编辑</button>
          <button type="button" :disabled="item.status !== 'ACTIVE' || item.is_default" @click="setDefault(item)"><Star :size="14" />{{ item.is_default ? '当前默认' : '设为默认' }}</button>
          <button type="button" @click="toggleModel(item)"><Power :size="14" />{{ item.status === 'ACTIVE' ? '停用' : '启用' }}</button>
          <button v-if="!item.is_account_api" class="model-card-delete" type="button" @click="deleteModel(item)"><Trash2 :size="14" />删除</button>
        </div>
      </article>
      <div v-if="activeType !== 'chat' && visibleModels.length === 0" class="model-empty model-empty--full"><Server :size="22" /><strong>暂无{{ modelTypeName(activeType) }}</strong></div>
    </section>
  </div>
</template>
