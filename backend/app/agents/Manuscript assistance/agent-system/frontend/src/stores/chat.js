import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useChatStore = defineStore('chat', () => {
  // 历史会话列表
  const conversations = ref([])

  // 当前会话消息
  const messages = ref([])

  // 当前选中的会话ID
  const currentConversationId = ref(null)

  // 是否正在生成
  const isGenerating = ref(false)

  // 当前工作状态
  const workStatus = ref(null) // { step, label }

  // 当前选中的智能体
  const currentAgent = ref(null)

  // 上传的文件列表（当前对话）
  const uploadedFiles = ref([])

  // 可用的智能体列表
  const agents = ref([
    { id: 'writing', name: 'Document Assistant', icon: '📝', description: '辅助论文各章节撰写与润色', available: true },
    { id: 'drawing', name: '绘图创作', icon: '🎨', description: '学术图表与示意图生成', available: false },
    { id: 'innovation', name: '创新挖掘', icon: '💡', description: '创新点分析与挖掘', available: false },
    { id: 'copyright', name: '软著文书', icon: '📄', description: '软件著作权文书生成', available: false },
    { id: 'patent', name: '专利文书', icon: '📋', description: '专利申请文书撰写', available: false },
    { id: 'translation', name: '学术翻译', icon: '🌐', description: '学术文本中英互译', available: false },
  ])

  // 按日期分组的历史会话
  const groupedConversations = computed(() => {
    const today = new Date().toISOString().slice(0, 10)
    const groups = { today: [], earlier: [] }

    conversations.value.forEach((conv) => {
      const convDate = conv.createdAt || conv.created_at || ''
      if (convDate.slice(0, 10) === today) {
        groups.today.push(conv)
      } else {
        groups.earlier.push(conv)
      }
    })

    return groups
  })

  // 从后端加载历史会话列表
  async function loadConversations() {
    try {
      const response = await fetch('/api/conversations')
      if (response.ok) {
        const data = await response.json()
        conversations.value = (data.conversations || []).map((c) => ({
          id: c.id,
          title: c.title,
          agent: c.agent_id,
          createdAt: c.created_at,
        }))
      }
    } catch (error) {
      console.error('加载历史会话失败:', error)
    }
  }

  // 从后端加载指定会话的消息
  async function loadMessages(conversationId) {
    try {
      const response = await fetch(`/api/conversations/${conversationId}/messages`)
      if (response.ok) {
        const data = await response.json()
        const rawMessages = data.messages || []
        const loadedMessages = []

        for (let i = 0; i < rawMessages.length; i++) {
          const msg = rawMessages[i]
          if (msg.role === 'user') {
            // 恢复思考步骤（后端保存的）
            const steps = (msg.thinking_steps || []).map((s) => ({
              key: s.key,
              label: s.label,
              detail: s.detail || '',
              active: false,
              done: true,
            }))
            loadedMessages.push({
              id: `${conversationId}-${i}`,
              role: 'user',
              content: msg.content,
              timestamp: msg.timestamp || new Date().toISOString(),
              thinkingSteps: steps,
              thinkingDuration: null,
            })
          } else {
            loadedMessages.push({
              id: `${conversationId}-${i}`,
              role: msg.role,
              content: msg.content,
              timestamp: msg.timestamp || new Date().toISOString(),
            })
          }
        }

        messages.value = loadedMessages
      }
    } catch (error) {
      console.error('加载消息记录失败:', error)
    }
  }

  // 初始化：加载历史
  loadConversations()

  // 上传文件（支持批量）
  async function uploadFile(file) {
    const formData = new FormData()
    formData.append('files', file)

    try {
      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const err = await response.json()
        throw new Error(err.detail || '上传失败')
      }

      const data = await response.json()
      // 支持批量返回
      const fileList = data.files || [data]
      for (const f of fileList) {
        uploadedFiles.value.push({
          id: f.file_id,
          name: f.filename,
          preview: f.content_preview,
          charCount: f.char_count,
        })
      }
      return data
    } catch (error) {
      console.error('文件上传失败:', error)
      throw error
    }
  }

  // 批量上传多个文件
  async function uploadFiles(files) {
    const formData = new FormData()
    for (const file of files) {
      formData.append('files', file)
    }

    try {
      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const err = await response.json()
        throw new Error(err.detail || '上传失败')
      }

      const data = await response.json()
      const fileList = data.files || []
      for (const f of fileList) {
        uploadedFiles.value.push({
          id: f.file_id,
          name: f.filename,
          preview: f.content_preview,
          charCount: f.char_count,
        })
      }
      return data
    } catch (error) {
      console.error('批量上传失败:', error)
      throw error
    }
  }

  // 移除已上传的文件
  function removeFile(fileId) {
    uploadedFiles.value = uploadedFiles.value.filter((f) => f.id !== fileId)
  }

  // 创建新会话
  function createConversation(agentId) {
    const agent = agents.value.find((a) => a.id === agentId)
    const newConv = {
      id: Date.now().toString(),
      title: '新对话',
      agent: agentId,
      createdAt: new Date().toISOString().slice(0, 10),
    }
    conversations.value.unshift(newConv)
    currentConversationId.value = newConv.id
    currentAgent.value = agent
    messages.value = []
    uploadedFiles.value = []
    return newConv
  }

  // 发送消息（流式）
  async function sendMessage(content) {
    const hasText = content && content.trim()
    const hasFiles = uploadedFiles.value.length > 0
    if ((!hasText && !hasFiles) || isGenerating.value) return

    const displayContent = hasText ? content.trim() : `[已上传 ${uploadedFiles.value.length} 个文件]`

    // 添加用户消息（含思考步骤占位）
    const userMsg = {
      id: Date.now().toString(),
      role: 'user',
      content: displayContent,
      files: [...uploadedFiles.value],
      timestamp: new Date().toISOString(),
      thinkingSteps: [],
      thinkingStartTime: Date.now(),
      thinkingDuration: null,
    }
    messages.value.push(userMsg)

    isGenerating.value = true
    workStatus.value = null

    // 准备 AI 消息占位
    const assistantMsgId = (Date.now() + 1).toString()
    messages.value.push({
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    })

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
        body: JSON.stringify({
          message: hasText ? content.trim() : '请根据上传的文件内容进行处理',
          conversation_id: currentConversationId.value,
          agent_id: currentAgent.value?.id || 'writing',
          file_ids: uploadedFiles.value.map((f) => f.id),
          language: 'zh',
        }),
      })

      if (!response.ok) {
        throw new Error(`请求失败: ${response.status}`)
      }

      // 解析 SSE 流
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // 按双换行分割 SSE 事件块，处理 \n\n 和 \r\n\r\n 两种情况
        const parts = buffer.split(/\n\n/)
        buffer = parts.pop() || ''

        for (const part of parts) {
          const trimmed = part.trim()
          if (!trimmed) continue

          const lines = trimmed.split('\n')
          let eventType = ''
          let dataStr = ''

          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim()
            } else if (line.startsWith('data:')) {
              dataStr = line.slice(5).trim()
            }
          }

          if (eventType && dataStr) {
            try {
              const data = JSON.parse(dataStr)
              handleSSEEvent(eventType, data, assistantMsgId)
            } catch (e) {
              console.warn('SSE parse error:', e, dataStr)
            }
          }
        }
      }

      // 处理 buffer 中可能残留的最后一个事件
      if (buffer.trim()) {
        const lines = buffer.trim().split('\n')
        let eventType = ''
        let dataStr = ''
        for (const line of lines) {
          if (line.startsWith('event:')) eventType = line.slice(6).trim()
          else if (line.startsWith('data:')) dataStr = line.slice(5).trim()
        }
        if (eventType && dataStr) {
          try {
            const data = JSON.parse(dataStr)
            handleSSEEvent(eventType, data, assistantMsgId)
          } catch (e) { /* ignore */ }
        }
      }

      // 清空上传文件
      uploadedFiles.value = []
    } catch (error) {
      console.error('发送消息失败:', error)
      const msg = messages.value.find((m) => m.id === assistantMsgId)
      if (msg) {
        msg.content = `⚠️ 请求失败：${error.message}\n\n请确认后端服务已启动（端口 8001）。`
      }
    } finally {
      isGenerating.value = false
      workStatus.value = null
    }
  }

  // 思考步骤详情映射
  const stepDetails = {
    intent: '分析用户输入，判断是润色、生成还是问答',
    parsing: '解析上传文件内容，提取关键段落与结构',
    searching: '检索 ArXiv 相关文献，获取参考资料',
    generating: '结合文献与上下文，生成专业润色建议',
  }

  // 处理 SSE 事件
  function handleSSEEvent(event, data, assistantMsgId) {
    switch (event) {
      case 'status': {
        workStatus.value = { step: data.step, label: data.label }

        // 同时更新用户消息的 thinkingSteps
        const userMsg = findLastUserMessage()
        if (userMsg) {
          const stepKey = data.step.replace('_done', '')
          const isDone = data.step.endsWith('_done')

          // 检查这个步骤是否已记录
          const existing = userMsg.thinkingSteps.find((s) => s.key === stepKey)
          if (existing) {
            existing.done = isDone
            existing.active = !isDone
            // 更新详情（如解析结果摘要）
            if (data.detail) {
              existing.detail = data.detail
            }
          } else {
            // 把之前的 active 步骤标记为 done
            userMsg.thinkingSteps.forEach((s) => {
              if (s.active) {
                s.active = false
                s.done = true
              }
            })
            // 添加新步骤
            userMsg.thinkingSteps.push({
              key: stepKey,
              label: data.label,
              detail: data.detail || stepDetails[stepKey] || '',
              active: !isDone,
              done: isDone,
            })
          }
        }
        break
      }
      case 'token': {
        // 通过 ID 找到响应式数组中的消息对象，直接修改响应式代理
        const msg = messages.value.find((m) => m.id === assistantMsgId)
        if (msg) {
          msg.content += data.content
        }
        break
      }
      case 'done': {
        workStatus.value = null
        if (data.conversation_id) {
          currentConversationId.value = data.conversation_id
        }
        // 刷新会话列表（新会话会在后端创建）
        loadConversations()
        // 标记思考完成，记录耗时
        const userMsg = findLastUserMessage()
        if (userMsg) {
          userMsg.thinkingSteps.forEach((s) => {
            s.active = false
            s.done = true
          })
          if (userMsg.thinkingStartTime) {
            userMsg.thinkingDuration = Date.now() - userMsg.thinkingStartTime
          }
        }
        break
      }
    }
  }

  // 找到最近一条用户消息
  function findLastUserMessage() {
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i].role === 'user') {
        return messages.value[i]
      }
    }
    return null
  }

  // 选中会话
  async function selectConversation(id) {
    currentConversationId.value = id
    messages.value = []
    uploadedFiles.value = []
    // 从后端加载该会话的消息
    await loadMessages(id)
    // 设置对应的 agent
    const conv = conversations.value.find((c) => c.id === id)
    if (conv) {
      const agent = agents.value.find((a) => a.id === conv.agent)
      if (agent) currentAgent.value = agent
    }
  }

  // 删除会话
  async function deleteConversation(id) {
    try {
      await fetch(`/api/conversations/${id}`, { method: 'DELETE' })
    } catch (error) {
      console.error('删除会话失败:', error)
    }
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (currentConversationId.value === id) {
      currentConversationId.value = null
      messages.value = []
    }
  }

  return {
    conversations,
    messages,
    currentConversationId,
    isGenerating,
    workStatus,
    currentAgent,
    agents,
    uploadedFiles,
    groupedConversations,
    loadConversations,
    loadMessages,
    uploadFile,
    uploadFiles,
    removeFile,
    createConversation,
    sendMessage,
    selectConversation,
    deleteConversation,
  }
})
