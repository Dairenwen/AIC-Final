import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,          // 监听 0.0.0.0，允许局域网 / 内网穿透访问
    port: 3000,
    allowedHosts: true,  // 允许任意 Host 头（ngrok/cloudflare 隧道域名需要）
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        // 禁用代理对 SSE 响应的缓冲
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.url?.includes('/stream')) {
              proxyReq.removeHeader('Accept-Encoding')
              proxyReq.setHeader('Accept', 'text/event-stream')
            }
          })
          proxy.on('proxyRes', (proxyRes, req, res) => {
            if (req.url?.includes('/stream')) {
              // 确保响应头阻止任何缓冲
              proxyRes.headers['cache-control'] = 'no-cache, no-store, must-revalidate'
              proxyRes.headers['x-accel-buffering'] = 'no'
              // 禁用压缩，防止 chunk 被合并
              delete proxyRes.headers['content-encoding']
              delete proxyRes.headers['content-length']
            }
          })
        },
      },
    },
  },
})
