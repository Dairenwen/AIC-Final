import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
// Order matters: tokens define the variables, base resets the document,
// primitives style the shared components, main.css holds the remaining
// per-view rules, layout.css owns the application frame, pages.css aligns
// the individual pages to one grid, and motion.css adds movement on top.
import './styles/tokens.css'
import './styles/base.css'
import './styles/primitives.css'
import './styles/main.css'
import './styles/layout.css'
import './styles/pages.css'
import './styles/motion.css'
import { initializeTheme } from './utils/theme'

initializeTheme()
createApp(App).use(createPinia()).use(router).mount('#app')
