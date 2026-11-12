import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // Defaults to a locally started Flask process.  When the backend runs
        // in the Docker stack instead, point this at its web entry, e.g.
        // ZHIYAN_DEV_API_TARGET=http://127.0.0.1:18080 npm run dev
        target: process.env.ZHIYAN_DEV_API_TARGET || 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
})
