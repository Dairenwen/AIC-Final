import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'file:///C:/Users/x1820/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'

const phone = process.env.ZHICY_TEST_PHONE
const password = process.env.ZHICY_TEST_PASSWORD
if (!phone || !password) throw new Error('Set ZHICY_TEST_PHONE and ZHICY_TEST_PASSWORD before running this check.')

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const output = path.join(root, 'output', 'ui')
await mkdir(output, { recursive: true })

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || path.join(process.env.LOCALAPPDATA, 'ms-playwright', 'chromium-1228', 'chrome-win64', 'chrome.exe')
const browser = await chromium.launch({ headless: true, executablePath })
const errors = []

try {
  const loginContext = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const loginPage = await loginContext.newPage()
  await loginPage.goto('http://127.0.0.1:5173/login', { waitUntil: 'networkidle' })
  await loginPage.getByPlaceholder('请输入手机号').fill(phone)
  await loginPage.getByPlaceholder('请输入密码').fill(password)
  await loginPage.locator('button[type="submit"]').click()
  await loginPage.waitForURL((url) => !url.pathname.startsWith('/login'))
  const storageState = await loginContext.storageState()
  await loginContext.close()

  for (const scenario of [
    { name: 'model-library-desktop', path: '/models', viewport: { width: 1440, height: 1000 } },
    { name: 'model-library-mobile', path: '/models', viewport: { width: 390, height: 844 } },
    { name: 'home-model-desktop', path: '/', viewport: { width: 1440, height: 1000 } },
    { name: 'home-model-mobile', path: '/', viewport: { width: 390, height: 844 } },
  ]) {
    const context = await browser.newContext({ viewport: scenario.viewport, storageState })
    const page = await context.newPage()
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`${scenario.name}: ${message.text()}`)
    })
    page.on('pageerror', (error) => errors.push(`${scenario.name}: ${error.message}`))
    await page.goto(`http://127.0.0.1:5173${scenario.path}`, { waitUntil: 'networkidle' })

    if (scenario.path === '/models') {
      await page.getByRole('heading', { name: '模型库' }).waitFor()
      await page.getByRole('heading', { name: '垂域模型' }).waitFor()
      const cardOverflow = await page.locator('.model-library-card').evaluateAll((cards) => (
        cards.some((card) => card.scrollWidth > card.clientWidth + 1)
      ))
      if (cardOverflow) errors.push(`${scenario.name}: model card content overflows`)
    } else {
      const selectedModel = await page.getByLabel('选择模型').inputValue()
      if (selectedModel !== 'vertical_domain') {
        errors.push(`${scenario.name}: expected vertical_domain, received ${selectedModel}`)
      }
    }

    const metrics = await page.evaluate(() => ({ bodyWidth: document.body.scrollWidth, viewportWidth: window.innerWidth }))
    if (metrics.bodyWidth > metrics.viewportWidth + 1) {
      errors.push(`${scenario.name}: horizontal overflow ${metrics.bodyWidth}/${metrics.viewportWidth}`)
    }
    await page.screenshot({ path: path.join(output, `${scenario.name}.png`), fullPage: true })
    await context.close()
  }

  const profileContext = await browser.newContext({ viewport: { width: 1280, height: 900 }, storageState })
  const profilePage = await profileContext.newPage()
  await profilePage.goto('http://127.0.0.1:5173/profile', { waitUntil: 'networkidle' })
  await profilePage.getByRole('button', { name: '模型库' }).click()
  await profilePage.getByRole('heading', { name: '模型库' }).waitFor()
  if (new URL(profilePage.url()).pathname !== '/profile') {
    errors.push('profile-model-panel: clicking model library navigated away from /profile')
  }
  await profilePage.screenshot({ path: path.join(output, 'profile-model-panel.png'), fullPage: true })
  await profileContext.close()

  const answerContext = await browser.newContext({ viewport: { width: 1280, height: 900 }, storageState })
  const chatPage = await answerContext.newPage()
  await chatPage.route('**/api/v1/chat', async (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data: { content: '对话模型显示检查', model: 'runtime-chat-model' } }),
  }))
  await chatPage.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await chatPage.getByLabel('科研任务内容').fill('检查对话模型名称')
  await chatPage.getByLabel('发送任务').click()
  await chatPage.getByText('runtime-chat-model', { exact: true }).waitFor()
  await chatPage.screenshot({ path: path.join(output, 'chat-current-model.png'), fullPage: true })
  await chatPage.close()

  const ragPage = await answerContext.newPage()
  await ragPage.route('**/api/v1/rag/answers', async (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      success: true,
      data: {
        answer: '知识库模型显示检查',
        status: 'NO_EVIDENCE',
        evidence: [],
        citations: [],
        documents: [],
        warnings: [],
        retrieval: { stages: [], candidate_count: 0 },
        model: 'runtime-rag-model',
      },
    }),
  }))
  await ragPage.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await ragPage.getByLabel('添加内容').click()
  await ragPage.getByRole('menuitem', { name: '知识库问答' }).click()
  await ragPage.getByLabel('科研任务内容').fill('检查知识库问答模型名称')
  await ragPage.getByLabel('发送任务').click()
  await ragPage.getByText('runtime-rag-model', { exact: true }).waitFor()
  await ragPage.screenshot({ path: path.join(output, 'rag-current-model.png'), fullPage: true })
  await ragPage.close()
  await answerContext.close()
} finally {
  await browser.close()
}

if (errors.length) {
  console.error(errors.join('\n'))
  process.exitCode = 1
} else {
  console.log('Model library and answer-model visual checks passed for desktop and mobile.')
}
