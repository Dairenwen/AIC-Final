<template>
  <div class="home">
    <!-- 顶部区域 -->
    <header class="home-header">
      <h1 class="home-title">你好，有什么可以帮你的？</h1>
      <p class="home-subtitle">选择一个智能体开始对话</p>
    </header>

    <!-- 智能体选择区域 -->
    <section class="agents-section">
      <div class="agents-grid">
        <div
          v-for="agent in chatStore.agents"
          :key="agent.id"
          class="agent-card"
          :class="{ disabled: !agent.available }"
          @click="handleSelectAgent(agent)"
        >
          <div class="agent-icon">{{ agent.icon }}</div>
          <div class="agent-info">
            <div class="agent-name">{{ agent.name }}</div>
            <div class="agent-desc">{{ agent.description }}</div>
          </div>
          <div v-if="!agent.available" class="agent-badge">即将上线</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'

const router = useRouter()
const chatStore = useChatStore()

function handleSelectAgent(agent) {
  if (!agent.available) return
  // 选择智能体 → 创建会话 → 直接进入对话页
  const conv = chatStore.createConversation(agent.id)
  router.push(`/chat/${conv.id}`)
}
</script>

<style scoped>
.home {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--bg-home);
  padding: 40px 24px;
}

.home-header {
  text-align: center;
  margin-bottom: 48px;
}

.home-title {
  font-size: var(--font-size-h1);
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.home-subtitle {
  font-size: 15px;
  color: var(--text-secondary);
}

/* 智能体卡片 */
.agents-section {
  width: 100%;
  max-width: 720px;
}

.agents-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.agent-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 14px;
  background: var(--bg-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}

.agent-card:hover:not(.disabled) {
  border-color: var(--color-accent-green);
  background: var(--color-light-green-bg);
  box-shadow: 0 2px 8px rgba(33, 76, 58, 0.06);
}

.agent-card.disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.agent-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.agent-info {
  text-align: center;
}

.agent-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.agent-desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}

.agent-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 10px;
  background: var(--border-default);
  color: var(--text-muted);
  padding: 2px 6px;
  border-radius: 3px;
}

@media (max-width: 768px) {
  .agents-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
