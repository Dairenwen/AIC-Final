import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/main.css'
import './styles/editorial-theme.css'
import { initializeTheme } from './utils/theme'

initializeTheme()
createApp(App).use(createPinia()).use(router).mount('#app')
