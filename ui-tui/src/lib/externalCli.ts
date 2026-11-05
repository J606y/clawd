import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { delimiter, resolve } from 'node:path'

export interface LaunchResult {
  code: null | number
  error?: string
}

const isHermesCommand = (bin: string) => {
  const base = bin.split(/[/\\]/).pop()?.toLowerCase() ?? ''

  return base === 'hermes' || base === 'hermes.exe'
}

/** Argv for a product command. clawd never falls through to a hermes console script. */
export const productCommandArgv = (args: string[]): string[] => {
  const python = process.env.HERMES_PYTHON?.trim()
  const root = process.env.HERMES_PYTHON_SRC_ROOT?.trim()
  const clawdBin = process.env.CLAWD_BIN?.trim()
  const clawdScript = root ? resolve(root, 'clawd') : ''
  const clawd = clawdBin && existsSync(clawdBin) ? clawdBin : existsSync(clawdScript) ? clawdScript : ''

  if (process.env.CLAWD_CLI === '1' || clawd) {
    if (python && clawd) {
      return [python, clawd, ...args]
    }

    if (python) {
      return [python, '-m', 'clawd_cli.main', ...args]
    }

    if (clawd) {
      return [clawd, ...args]
    }
  }

  const configured = process.env.HERMES_BIN?.trim()

  if (configured && !isHermesCommand(configured)) {
    return [configured, ...args]
  }

  const onPath = process.env.PATH?.split(delimiter).find(dir => existsSync(resolve(dir, process.platform === 'win32' ? 'clawd.exe' : 'clawd')))

  if (onPath) {
    return [resolve(onPath, process.platform === 'win32' ? 'clawd.exe' : 'clawd'), ...args]
  }

  return [configured || 'hermes', ...args]
}

export const launchHermesCommand = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolvePromise => {
    const argv = productCommandArgv(args)
    const child = spawn(argv[0]!, argv.slice(1), { stdio: 'inherit' })

    child.on('error', err => resolvePromise({ code: null, error: err.message }))
    child.on('exit', code => resolvePromise({ code }))
  })
