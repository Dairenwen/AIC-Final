<template>
  <aside class="sidebar">
    <!-- Logo 区域 -->
    <div class="sidebar-header">
      <div class="logo">
        <span class="logo-icon">✦</span>
        <span class="logo-text">智能写作助手</span>
      </div>
      <button class="btn-new" @click="handleNewChat" title="新建对话">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>
    </div>

    <!-- 历史记录 -->
    <div class="sidebar-body">
      <div v-if="chatStore.groupedConversations.today.length" class="history-group">
        <div class="group-label">今天</div>
        <div
          v-for="conv in chatStore.groupedConversations.today"
          :key="conv.id"
          class="history-item"
          :class="{ active: chatStore.currentConversationId === conv.id }"
          @click="handleSelect(conv.id)"
        >
          <span class="item-title">{{ conv.title }}</span>
          <button class="btn-delete" @click.stop="chatStore.deleteConversation(conv.id)" title="删除">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3,6 5,6 21,6" />
              <path d="M19,6v14a2,2,0,0,1-2,2H7a2,2,0,0,1-2-2V6M8,6V4a2,2,0,0,1,2-2h4a2,2,0,0,1,2,2V6" />
            </svg>
          </button>
        </div>
      </div>

      <div v-if="chatStore.groupedConversations.earlier.length" class="history-group">
        <div class="group-label">更早</div>
        <div
          v-for="conv in chatStore.groupedConversations.earlier"
          :key="conv.id"
          class="history-item"
          :class="{ active: chatStore.currentConversationId === conv.id }"
          @click="handleSelect(conv.id)"
        >
          <span class="item-title">{{ conv.title }}</span>
          <button class="btn-delete" @click.stop="chatStore.deleteConversation(conv.id)" title="删除">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3,6 5,6 21,6" />
              <path d="M19,6v14a2,2,0,0,1-2,2H7a2,2,0,0,1-2-2V6M8,6V4a2,2,0,0,1,2-2h4a2,2,0,0,1,2,2V6" />
            </svg>
          </button>
        </div>
      </div>

      <div v-if="!chatStore.conversations.length" class="empty-history">
        <p>暂无对话记录</p>
      </div>
    </div>

    <!-- 底部用户区 -->
    <div class="sidebar-footer">
      <div class="user-info">
        <div class="user-avatar">U</div>
        <span class="user-name">用户</span>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'

const router = useRouter()
const chatStore = useChatStore()

function handleNewChat() {
  // 回到首页重新选择智能体
  chatStore.currentAgent = null
  router.push('/')
}

function handleSelect(id) {
  chatStore.selectConversation(id)
  router.push(`/chat/${id}`)
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background-color: var(--color-dark-green);
  display: flex;
  flex-direction: column;
  color: #fff;
  flex-shrink: 0;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logo-icon {
  font-size: 20px;
  color: var(--color-brand-yellow);
}

.logo-text {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0;
}

.btn-new {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: rgba(255, 255, 255, 0.7);
  transition: all 0.15s;
}

.btn-new:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.sidebar-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 8px;
}

.history-group {
  margin-bottom: 16px;
}

.group-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
  padding: 4px 10px 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s;
}

.history-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.history-item.active {
  background: rgba(255, 255, 255, 0.12);
}

.item-title {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.85);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.btn-delete {
  opacity: 0;
  color: rgba(255, 255, 255, 0.4);
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  flex-shrink: 0;
  transition: all 0.15s;
}

.history-item:hover .btn-delete {
  opacity: 1;
}

.btn-delete:hover {
  color: var(--color-error);
  background: rgba(255, 255, 255, 0.1);
}

.empty-history {
  text-align: center;
  padding: 40px 16px;
  color: rgba(255, 255, 255, 0.3);
  font-size: 13px;
}

.sidebar-footer {
  padding: 14px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--color-accent-green);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 500;
}

.user-name {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.75);
}
</style>
