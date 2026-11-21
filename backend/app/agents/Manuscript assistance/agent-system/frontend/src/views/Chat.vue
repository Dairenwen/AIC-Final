<template>
  <div class="chat-view">
    <!-- 主聊天区域 -->
    <div class="chat-main">
      <!-- 顶部导航栏 -->
      <header class="chat-header">
        <div class="chat-header-left">
          <span v-if="chatStore.currentAgent" class="header-agent-icon">{{ chatStore.currentAgent.icon }}</span>
          <span class="header-title">{{ chatStore.currentAgent?.name || 'Document Assistant' }}</span>
        </div>
      </header>

      <!-- 消息区域 -->
      <div class="chat-messages" ref="messagesContainer">
        <!-- 空状态 -->
        <div v-if="!chatStore.messages.length" class="chat-empty">
          <div class="empty-icon">📝</div>
          <p class="empty-text">开始你的论文写作之旅</p>
          <p class="empty-hint">上传初稿文件或粘贴文本，我来帮你润色优化</p>
          <div class="empty-tips">
            <div class="tip-item">📄 上传论文初稿，逐段润色</div>
            <div class="tip-item">✍️ 粘贴段落内容，精准修改</div>
            <div class="tip-item">💬 描述写作需求，获取建议</div>
          </div>
        </div>

        <!-- 消息列表 -->
        <template v-for="(msg, msgIndex) in chatStore.messages" :key="msg.id">
          <!-- 用户消息：靠右 -->
          <div v-if="msg.role === 'user'" class="message user">
            <div class="message-body">
              <div v-if="msg.files && msg.files.length" class="message-files">
                <div v-for="f in msg.files" :key="f.id" class="file-tag">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14,2H6A2,2,0,0,0,4,4V20a2,2,0,0,0,2,2H18a2,2,0,0,0,2-2V8Z" />
                    <polyline points="14,2 14,8 20,8" />
                  </svg>
                  <span>{{ f.name }}</span>
                </div>
              </div>
              <div class="message-content" v-html="renderMarkdown(msg.content)"></div>
              <div class="message-time">{{ formatTime(msg.timestamp) }}</div>
            </div>
            <div class="message-avatar">
              <span>U</span>
            </div>
          </div>

          <!-- AI 回复：靠左，思考过程在回复上方 -->
          <div v-if="msg.role === 'assistant'" class="message-group assistant-group">
            <!-- 思考过程：始终显示在 AI 回复上方 -->
            <div
              v-if="getThinkingForUserBefore(msgIndex)"
              class="thinking-block"
            >
              <div class="thinking-header" @click="toggleThinking(msg.id)">
                <div class="thinking-icon" :class="{ spinning: isThinkingInProgress(msgIndex) }">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" />
                    <path d="M12 6v6l4 2" />
                  </svg>
                </div>
                <span class="thinking-title">
                  {{ isThinkingInProgress(msgIndex) ? '思考中...' : '已思考' }}
                  <span v-if="!isThinkingInProgress(msgIndex) && getThinkingDurationForAssistant(msgIndex)" class="thinking-duration">
                    (用时 {{ getThinkingDurationForAssistant(msgIndex) }})
                  </span>
                </span>
                <svg
                  class="thinking-chevron"
                  :class="{ expanded: isThinkingExpanded(msg.id) }"
                  width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                >
                  <polyline points="6,9 12,15 18,9" />
                </svg>
              </div>
              <transition name="expand">
                <div v-if="isThinkingExpanded(msg.id)" class="thinking-body">
                  <div class="thinking-steps">
                    <div
                      v-for="(step, index) in getThinkingStepsForAssistant(msgIndex)"
                      :key="index"
                      class="thinking-step"
                      :class="{ active: step.active, done: step.done }"
                    >
                      <div class="thinking-step-dot">
                        <span v-if="step.done" class="step-icon done">✓</span>
                        <span v-else-if="step.active" class="step-icon active"></span>
                        <span v-else class="step-icon pending"></span>
                      </div>
                      <div class="thinking-step-content">
                        <span class="thinking-step-label">{{ step.label }}</span>
                        <span v-if="step.detail" class="thinking-step-detail">{{ step.detail }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </transition>
            </div>

            <!-- 每条助手消息只渲染一个气泡；无文本时在该气泡内显示加载状态。 -->
            <div class="message assistant">
              <div class="message-avatar">
                <span class="assistant-avatar">✦</span>
              </div>
              <div class="message-body">
                <div v-if="msg.content" class="message-content" v-html="renderMarkdown(msg.content)"></div>
                <div v-else class="typing-indicator">
                  <span></span><span></span><span></span>
                </div>
                <div v-if="msg.content" class="message-time">{{ formatTime(msg.timestamp) }}</div>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 底部输入区域 -->
      <div class="chat-composer">
        <div class="composer-box">
          <!-- 已上传文件列表 -->
          <div v-if="chatStore.uploadedFiles.length" class="uploaded-files">
            <div v-for="f in chatStore.uploadedFiles" :key="f.id" class="uploaded-file-tag">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14,2H6A2,2,0,0,0,4,4V20a2,2,0,0,0,2,2H18a2,2,0,0,0,2-2V8Z" />
                <polyline points="14,2 14,8 20,8" />
              </svg>
              <span class="file-name">{{ f.name }}</span>
              <span class="file-size">{{ formatSize(f.charCount) }}</span>
              <button class="file-remove" @click="chatStore.removeFile(f.id)">×</button>
            </div>
          </div>

          <div class="composer-input-row">
            <!-- + 号按钮 -->
            <div class="attach-wrapper">
              <button class="btn-attach" title="更多操作" @click="showAttachMenu = !showAttachMenu; showAgentSubMenu = false">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>

              <!-- 一级菜单 -->
              <div v-if="showAttachMenu && !showAgentSubMenu" class="attach-menu">
                <div class="attach-menu-item" @click="triggerFileInput">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14,2H6A2,2,0,0,0,4,4V20a2,2,0,0,0,2,2H18a2,2,0,0,0,2-2V8Z" />
                    <polyline points="14,2 14,8 20,8" />
                  </svg>
                  <span>上传文件</span>
                </div>
                <div class="attach-menu-item" @click="showAgentSubMenu = true">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" />
                    <path d="M8,14s1.5,2,4,2,4-2,4-2" />
                    <line x1="9" y1="9" x2="9.01" y2="9" />
                    <line x1="15" y1="9" x2="15.01" y2="9" />
                  </svg>
                  <span>切换智能体</span>
                  <svg class="arrow-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="9,18 15,12 9,6" />
                  </svg>
                </div>
              </div>

              <!-- 二级菜单：智能体列表 -->
              <div v-if="showAttachMenu && showAgentSubMenu" class="attach-menu">
                <div class="attach-menu-item back-item" @click="showAgentSubMenu = false">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="15,18 9,12 15,6" />
                  </svg>
                  <span>返回</span>
                </div>
                <div class="attach-menu-divider"></div>
                <div
                  v-for="agent in chatStore.agents"
                  :key="agent.id"
                  class="attach-menu-item agent-item"
                  :class="{ active: chatStore.currentAgent?.id === agent.id, disabled: !agent.available }"
                  @click="handleSwitchAgent(agent)"
                >
                  <span class="agent-menu-icon">{{ agent.icon }}</span>
                  <span class="agent-menu-name">{{ agent.name }}</span>
                  <span v-if="!agent.available" class="agent-menu-badge">待开发</span>
                  <span v-else-if="chatStore.currentAgent?.id === agent.id" class="agent-menu-check">✓</span>
                </div>
              </div>
            </div>

            <input
              ref="fileInputRef"
              type="file"
              accept=".pdf,.txt,.md,.docx,.tex"
              multiple
              style="display: none"
              @change="handleFileUpload"
            />

            <textarea
              v-model="inputText"
              class="composer-textarea"
              placeholder="粘贴初稿内容或输入需求..."
              rows="1"
              @keydown.enter.exact="handleSend"
              @input="autoResize"
              ref="textareaRef"
            ></textarea>

            <button class="btn-send" :disabled="!canSend" @click="handleSend">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22,2 15,22 11,13 2,9 22,2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, reactive } from 'vue'
import { useChatStore } from '../stores/chat'

const chatStore = useChatStore()

const inputText = ref('')
const messagesContainer = ref(null)
const textareaRef = ref(null)
const fileInputRef = ref(null)
const isUploading = ref(false)
const showAttachMenu = ref(false)
const showAgentSubMenu = ref(false)

// 思考过程展开状态
const expandedThinkings = reactive({})
// 默认展开当前进行中的思考
expandedThinkings['current'] = true

// 工作流水线步骤定义（更详细）
const pipelineSteps = [
  { key: 'intent', label: '意图识别', detail: '分析用户输入，判断是润色、生成还是问答' },
  { key: 'parsing', label: '文稿解析', detail: '解析上传文件内容，提取关键段落与结构' },
  { key: 'searching', label: '文献检索', detail: '检索 ArXiv 相关文献，获取参考资料' },
  { key: 'generating', label: '组织回复', detail: '结合文献与上下文，生成专业润色建议' },
]

// 找到 AI 消息前面对应的用户消息
function getUserMsgBeforeAssistant(msgIndex) {
  for (let i = msgIndex - 1; i >= 0; i--) {
    if (chatStore.messages[i].role === 'user') {
      return chatStore.messages[i]
    }
  }
  return null
}

// 判断 AI 消息前面的用户消息是否有思考步骤
function getThinkingForUserBefore(msgIndex) {
  const userMsg = getUserMsgBeforeAssistant(msgIndex)
  return userMsg && userMsg.thinkingSteps && userMsg.thinkingSteps.length > 0
}

// 判断该 AI 消息对应的思考是否还在进行中
function isThinkingInProgress(msgIndex) {
  const userMsg = getUserMsgBeforeAssistant(msgIndex)
  if (!userMsg || !userMsg.thinkingSteps) return false
  return userMsg.thinkingSteps.some(s => s.active)
}

// 获取 AI 消息对应的思考步骤
function getThinkingStepsForAssistant(msgIndex) {
  const userMsg = getUserMsgBeforeAssistant(msgIndex)
  return userMsg?.thinkingSteps || []
}

// 获取 AI 消息对应的思考耗时
function getThinkingDurationForAssistant(msgIndex) {
  const userMsg = getUserMsgBeforeAssistant(msgIndex)
  if (!userMsg?.thinkingDuration) return ''
  const secs = Math.round(userMsg.thinkingDuration / 1000)
  return `${secs} 秒`
}

function toggleThinking(id) {
  expandedThinkings[id] = !expandedThinkings[id]
}

function isThinkingExpanded(id) {
  // 默认展开
  if (expandedThinkings[id] === undefined) return true
  return expandedThinkings[id]
}

const canSend = computed(() => {
  return (inputText.value.trim() || chatStore.uploadedFiles.length) && !chatStore.isGenerating
})

// 当生成开始时自动展开当前思考
watch(() => chatStore.isGenerating, (val) => {
  if (val) {
    // 新回复生成时默认展开思考
  }
})

function handleSend(e) {
  if (e && e.shiftKey) return
  e?.preventDefault()
  if (!canSend.value) return

  chatStore.sendMessage(inputText.value || '')
  inputText.value = ''

  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto'
  }
}

function triggerFileInput() {
  showAttachMenu.value = false
  fileInputRef.value?.click()
}

function handleSwitchAgent(agent) {
  if (!agent.available) return
  chatStore.currentAgent = agent
  showAttachMenu.value = false
  showAgentSubMenu.value = false
}

async function handleFileUpload(e) {
  const files = e.target.files
  if (!files || files.length === 0) return

  isUploading.value = true
  try {
    await chatStore.uploadFiles(files)
  } catch (err) {
    alert(`上传失败: ${err.message}`)
  } finally {
    isUploading.value = false
    e.target.value = ''
  }
}

function autoResize(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 150) + 'px'
}

function renderMarkdown(content) {
  if (!content) return ''
  let html = content
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/### (.*?)(\n|$)/g, '<h4>$1</h4>')
    .replace(/## (.*?)(\n|$)/g, '<h3>$1</h3>')
    .replace(/# (.*?)(\n|$)/g, '<h2>$1</h2>')
    // 处理 markdown 格式的链接 [text](url)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')

  // 处理裸链接（不在 href="" 或 >...</a> 中的）
  html = html.replace(/(^|[^"'>])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')

  html = html.replace(/\n/g, '<br>')
  return html
}

function formatTime(timestamp) {
  if (!timestamp) return ''
  const d = new Date(timestamp)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatSize(charCount) {
  if (charCount > 10000) return `${(charCount / 10000).toFixed(1)}万字`
  if (charCount > 1000) return `${(charCount / 1000).toFixed(1)}千字`
  return `${charCount}字`
}

// 自动滚动到底部
watch(
  () => chatStore.messages.map((m) => m.content).join(''),
  () => {
    nextTick(() => {
      if (messagesContainer.value) {
        messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
      }
    })
  }
)
</script>

<style scoped>
.chat-view {
  flex: 1;
  display: flex;
  height: 100vh;
  background: var(--bg-main);
}

/* 主聊天区 */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border-default);
  flex-shrink: 0;
}

.chat-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-agent-icon { font-size: 18px; }
.header-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }

/* 消息区域 */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 24px;
  max-width: 800px;
  width: 100%;
  margin: 0 auto;
}

.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
}

.empty-icon { font-size: 48px; margin-bottom: 16px; }
.empty-text { font-size: 18px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
.empty-hint { font-size: 14px; color: var(--text-muted); margin-bottom: 24px; }

.empty-tips {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tip-item {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 8px 16px;
  background: var(--color-light-green-bg);
  border-radius: var(--radius-sm);
}

/* ===== 消息布局 ===== */
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

/* AI 回复组（思考 + 回复在一起） */
.message-group.assistant-group {
  margin-bottom: 20px;
}

/* 用户消息靠右 */
.message.user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 32px; height: 32px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; flex-shrink: 0;
}

.message.user .message-avatar { background: var(--color-accent-green); color: #fff; }
.message.assistant .message-avatar { background: var(--color-brand-yellow); color: var(--color-dark-green); }
.assistant-avatar { font-size: 16px; }

.message-body { max-width: 75%; min-width: 0; }

.message.user .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.message.assistant .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.message-files {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px;
}

.file-tag {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: var(--color-accent-green);
  background: var(--color-light-green-bg);
  padding: 4px 8px; border-radius: 4px;
}

.message-content {
  font-size: 15px; line-height: 1.72; color: var(--text-primary);
  padding: 10px 14px; border-radius: var(--radius-md);
}

.message.user .message-content {
  background: var(--color-bubble-green);
  border-radius: var(--radius-lg);
}

.message.assistant .message-content {
  background: var(--bg-panel);
  border: 1px solid var(--border-default);
}

.message-content :deep(h2) { font-size: 18px; margin: 16px 0 8px; }
.message-content :deep(h3) { font-size: 16px; margin: 12px 0 6px; }
.message-content :deep(h4) { font-size: 15px; font-weight: 600; margin: 10px 0 4px; }
.message-content :deep(code) {
  background: var(--bg-body); padding: 2px 5px; border-radius: 3px; font-size: 13px;
}
.message-content :deep(strong) { font-weight: 600; }
.message-content :deep(a) {
  color: var(--color-accent-green);
  text-decoration: underline;
  word-break: break-all;
}
.message-content :deep(a:hover) {
  opacity: 0.8;
}

.message-time {
  font-size: 11px; color: var(--text-muted); margin-top: 4px;
}

.message.user .message-time { text-align: right; padding-right: 14px; }
.message.assistant .message-time { padding-left: 14px; }

/* ===== 思考过程块（类似 DeepSeek） ===== */
.thinking-block {
  margin: 0 0 12px;
  padding-left: 44px; /* 与 AI 头像对齐 */
}

.assistant-group .message.assistant {
  margin-bottom: 0;
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-panel-soft);
  border: 1px solid var(--border-default);
  transition: background 0.15s;
  user-select: none;
}

.thinking-header:hover {
  background: var(--color-light-green-bg);
}

.thinking-icon {
  display: flex;
  align-items: center;
  color: var(--color-success);
}

.thinking-icon.spinning svg {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.thinking-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  flex: 1;
}

.thinking-duration {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 400;
}

.thinking-chevron {
  color: var(--text-muted);
  transition: transform 0.2s;
}

.thinking-chevron.expanded {
  transform: rotate(180deg);
}

.thinking-body {
  margin-top: 8px;
  padding: 12px 16px;
  background: var(--bg-panel-soft);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.thinking-steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.thinking-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 0;
}

.thinking-step-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-body);
  border: 2px solid var(--border-default);
  flex-shrink: 0;
  margin-top: 1px;
  transition: all 0.3s;
}

.thinking-step.active .thinking-step-dot {
  border-color: var(--color-success);
  background: var(--color-light-green-bg);
}

.thinking-step.done .thinking-step-dot {
  border-color: var(--color-success);
  background: var(--color-success);
}

.step-icon.done {
  font-size: 10px;
  color: #fff;
  font-weight: bold;
}

.step-icon.active {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
  animation: pulse 1.2s infinite;
}

.step-icon.pending {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--border-default);
}

.thinking-step-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.thinking-step-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.thinking-step.active .thinking-step-label {
  color: var(--color-success);
  font-weight: 600;
}

.thinking-step.done .thinking-step-label {
  color: var(--color-success-dark);
}

.thinking-step-detail {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.4); opacity: 0.6; }
}

/* 展开动画 */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.25s ease;
  max-height: 300px;
}

.expand-enter-from,
.expand-leave-to {
  max-height: 0;
  opacity: 0;
  margin-top: 0;
  padding: 0 16px;
}

/* 打字动画 */
.typing-indicator {
  display: flex; gap: 4px; padding: 14px 16px;
  background: var(--bg-panel); border: 1px solid var(--border-default);
  border-radius: var(--radius-md); width: fit-content;
}

.typing-indicator span {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--color-accent-green); animation: typing 1.2s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1); }
}

/* 底部输入 */
.chat-composer {
  flex-shrink: 0;
  padding: 16px 24px 24px;
  background: var(--bg-main);
}

.composer-box {
  max-width: 760px;
  margin: 0 auto;
  background: var(--bg-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 6px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}

.uploaded-files {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 8px 10px 4px;
}

.uploaded-file-tag {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: var(--color-accent-green);
  background: var(--color-light-green-bg);
  padding: 4px 8px; border-radius: 4px;
}

.file-name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-size { color: var(--text-muted); }
.file-remove {
  font-size: 14px; color: var(--text-muted); cursor: pointer;
  margin-left: 2px; line-height: 1;
}
.file-remove:hover { color: var(--color-error); }

.composer-input-row {
  display: flex; align-items: flex-end; gap: 8px; padding: 6px 8px;
}

.btn-attach {
  width: 34px; height: 34px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted); transition: all 0.15s; flex-shrink: 0;
}
.btn-attach:hover { background: var(--color-light-green-bg); color: var(--color-accent-green); }

.attach-wrapper {
  position: relative;
  flex-shrink: 0;
}

.attach-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 220px;
  background: var(--bg-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  z-index: 100;
  overflow: hidden;
  padding: 4px 0;
}

.attach-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-primary);
  transition: background 0.12s;
}

.attach-menu-item:hover:not(.disabled) {
  background: var(--color-light-green-bg);
}

.attach-menu-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.attach-menu-item.active {
  background: var(--color-light-green-bg);
}

.attach-menu-divider {
  height: 1px;
  background: var(--border-default);
  margin: 4px 0;
}

.agent-menu-icon { font-size: 16px; }
.agent-menu-name { flex: 1; }
.agent-menu-badge {
  font-size: 10px;
  color: var(--text-muted);
  background: var(--bg-body);
  padding: 2px 5px;
  border-radius: 3px;
}
.agent-menu-check {
  font-size: 13px;
  color: var(--color-success);
  font-weight: 600;
}

.arrow-icon {
  margin-left: auto;
  color: var(--text-muted);
}

.back-item {
  color: var(--text-muted);
  font-size: 13px;
}
.back-item:hover {
  color: var(--text-primary);
}

.composer-textarea {
  flex: 1; resize: none; min-height: 24px; max-height: 150px;
  padding: 6px 8px; font-size: 15px; line-height: 1.5;
  color: var(--text-primary); background: transparent; overflow-y: auto;
}
.composer-textarea::placeholder { color: var(--text-muted); }

.btn-send {
  width: 34px; height: 34px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--color-dark-green); color: #fff;
  transition: all 0.15s; flex-shrink: 0;
}
.btn-send:hover:not(:disabled) { background: var(--color-mid-green); }
.btn-send:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
