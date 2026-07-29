import { spawnSync } from 'node:child_process'
import process from 'node:process'

const steps = [
  ['node_modules/vue-tsc/index.js', '-b'],
  ['node_modules/vite/bin/vite.js', 'build'],
]

for (const args of steps) {
  const result = spawnSync(process.execPath, args, { stdio: 'inherit' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
