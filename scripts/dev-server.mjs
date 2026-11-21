import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(scriptDir, '..')
const env = { ...process.env }
const inheritedPath = env.Path || env.PATH || ''
delete env.Path
delete env.PATH
env.PATH = inheritedPath

const processes = [
  spawn(path.join(root, 'backend', '.venv', 'Scripts', 'python.exe'), ['wsgi.py'], {
    cwd: path.join(root, 'backend'),
    env,
    stdio: 'inherit',
  }),
  spawn(
    'C:\\Users\\x1820\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe',
    [path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '0.0.0.0', '--port', '5173', '--strictPort'],
    { cwd: path.join(root, 'frontend'), env, stdio: 'inherit' },
  ),
]

for (const child of processes) {
  child.on('exit', (code) => {
    if (code && code !== 0) {
      console.error(`Development process exited with code ${code}.`)
      shutdown(code)
    }
  })
}

function shutdown(code = 0) {
  for (const child of processes) child.kill()
  process.exit(code)
}

process.on('SIGINT', () => shutdown())
process.on('SIGTERM', () => shutdown())

console.log('Starting Zhiyan development services...')
console.log('Frontend: http://localhost:5173')
console.log('Backend:  http://localhost:5000/api/v1/health/live')
console.log('Knowledge: http://localhost:5000/api/v1/knowledge-base/ui (system_admin only)')

setInterval(() => {}, 60_000)
