import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/Home.vue'),
  },
  {
    path: '/chat/:id?',
    name: 'Chat',
    component: () => import('../views/Chat.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：进入 Chat 页面必须已选择智能体
router.beforeEach((to, from, next) => {
  if (to.name === 'Chat') {
    // 动态导入 store 检查
    import('../stores/chat').then(({ useChatStore }) => {
      const chatStore = useChatStore()
      if (!chatStore.currentAgent) {
        next({ name: 'Home' })
      } else {
        next()
      }
    })
  } else {
    next()
  }
})

export default router
