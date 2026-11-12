import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const python = process.env.ZHIYAN_PYTHON ? path.resolve(root, process.env.ZHIYAN_PYTHON) : path.join(root, 'backend', '.venv',
  process.platform === 'win32' ? 'Scripts' : 'bin',
  process.platform === 'win32' ? 'python.exe' : 'python')
const vite = path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js')
if (!existsSync(python) || !existsSync(vite)) {
  console.error('请先创建 backend/.venv 并安装后端依赖，再在 frontend 安装依赖。可用 ZHIYAN_PYTHON 指定解释器。')
  process.exit(1)
}
const env = { ...process.env }
env.PATH = [path.dirname(python), env.PATH || env.Path || ""].join(path.delimiter)
delete env.Path
const children = []
let stopping = false
function shutdown(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) child.kill()
  process.exitCode = code
}
for (const [command, args, cwd] of [
  [python, ['wsgi.py'], path.join(root, 'backend')],
  [process.execPath, [vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort'], path.join(root, 'frontend')],
]) {
  const child = spawn(command, args, { cwd, env, stdio: 'inherit' })
  children.push(child)
  child.on('error', (error) => {
    console.error(`启动失败：${error.message}`)
    shutdown(1)
  })
  child.on('exit', (code, signal) => {
    if (!stopping) shutdown(code || (signal ? 1 : 0))
  })
}
process.on('SIGINT', () => shutdown())
process.on('SIGTERM', () => shutdown())
console.log('Frontend: http://localhost:5173')
console.log('Backend:  http://localhost:5000/api/v1/health/live')
