import { spawn } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'file:///C:/Users/x1820/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(scriptDir, '..')
const output = path.join(root, 'output', 'ui')
await mkdir(output, { recursive: true })

const env = { ...process.env }
const inheritedPath = env.Path || env.PATH || ''
delete env.Path
delete env.PATH
env.PATH = inheritedPath

const backend = spawn(path.join(root, 'backend', '.venv', 'Scripts', 'python.exe'), ['wsgi.py'], {
  cwd: path.join(root, 'backend'),
  env,
  stdio: 'ignore',
})
const frontend = spawn(
  'C:\\Users\\x1820\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe',
  [path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '127.0.0.1', '--port', '5173'],
  { cwd: path.join(root, 'frontend'), env, stdio: 'ignore' },
)

async function waitFor(url) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // The server may still be starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Server did not become ready: ${url}`)
}

const browser = await chromium.launch({ headless: true })
const errors = []

try {
  await Promise.all([
    waitFor('http://127.0.0.1:5000/api/v1/health/live'),
    waitFor('http://127.0.0.1:5173'),
  ])

  const scenarios = [
    { name: 'home-desktop', path: '/', viewport: { width: 1440, height: 900 } },
    { name: 'agents-desktop', path: '/agents', viewport: { width: 1440, height: 900 } },
    { name: 'knowledge-desktop', path: '/knowledge', viewport: { width: 1440, height: 900 } },
    { name: 'profile-desktop', path: '/profile', viewport: { width: 1440, height: 900 } },
    { name: 'home-mobile', path: '/', viewport: { width: 390, height: 844 } },
    { name: 'agents-mobile', path: '/agents', viewport: { width: 390, height: 844 } },
  ]

  for (const scenario of scenarios) {
    const page = await browser.newPage({ viewport: scenario.viewport })
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`${scenario.name}: ${message.text()}`)
    })
    page.on('pageerror', (error) => errors.push(`${scenario.name}: ${error.message}`))
    await page.goto(`http://127.0.0.1:5173${scenario.path}`, { waitUntil: 'networkidle' })
    await page.screenshot({ path: path.join(output, `${scenario.name}.png`), fullPage: true })
    const metrics = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: window.innerWidth,
      bodyHeight: document.body.scrollHeight,
      textLength: document.body.innerText.trim().length,
    }))
    if (metrics.bodyWidth > metrics.viewportWidth + 1) {
      errors.push(`${scenario.name}: horizontal overflow ${metrics.bodyWidth}/${metrics.viewportWidth}`)
    }
    if (metrics.textLength < 30 || metrics.bodyHeight < 300) {
      errors.push(`${scenario.name}: page appears blank or incomplete`)
    }
    await page.close()
  }

  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await page.getByLabel('科研任务内容').fill('检索多智能体科研助手相关论文')
  await page.getByLabel('发送任务').click()
  await page.getByText('框架演示任务已完成').first().waitFor({ timeout: 8000 })
  await page.close()
} finally {
  await browser.close()
  backend.kill()
  frontend.kill()
}

if (errors.length) {
  console.error(errors.join('\n'))
  process.exitCode = 1
} else {
  console.log('Visual checks passed: 6 screenshots, no console errors or horizontal overflow.')
}
