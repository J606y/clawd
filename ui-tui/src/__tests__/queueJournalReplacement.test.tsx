import { chmodSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'

import { renderSync, Text } from '@hermes/ink'
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { useComposerState } from '../app/useComposerState.js'
import { useSubmission } from '../app/useSubmission.js'
import type { QueueItem } from '../hooks/useQueue.js'
import { useQueue } from '../hooks/useQueue.js'
import * as pendingInputs from '../lib/pendingInputs.js'

// Faults fire inside the real `savePendingInput` / `removePendingInput` bindings.
// They are not a substitute for those functions.
const fsGate = vi.hoisted(() => ({
  write: null as ((path: string) => void) | null,
  rename: null as ((from: string, to: string) => void) | null,
  remove: null as ((path: string) => void) | null
}))

vi.mock('node:fs', async importActual => {
  const actual = await importActual<typeof import('node:fs')>()

  return {
    ...actual,
    writeFileSync: (...args: any[]) => {
      fsGate.write?.(String(args[0]))

      return (actual.writeFileSync as any)(...args)
    },
    renameSync: (from: any, to: any) => {
      fsGate.rename?.(String(from), String(to))

      return actual.renameSync(from, to)
    },
    rmSync: (path: any, options?: any) => {
      fsGate.remove?.(String(path))

      return actual.rmSync(path, options)
    }
  }
})

const attachments = [{ mime: 'image/png', path: '/w/shot.png' }]
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nL8AAAAASUVORK5CYII=', 'base64')

function fsError(code: 'EACCES' | 'ENOSPC', syscall: string, path: string) {
  return Object.assign(new Error(`${code}: ${syscall} '${path}'`), { code, syscall, path })
}

function clearFsGate() {
  fsGate.write = null
  fsGate.rename = null
  fsGate.remove = null
}

function journalRecords(home: string) {
  const dir = join(home, 'tui-pending-inputs')

  return readdirSync(dir)
    .filter(name => /^[0-9a-f-]{36}\.json$/.test(name))
    .map(name => JSON.parse(readFileSync(join(dir, name), 'utf8')))
}

function mountQueue() {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'ink-queue-replace-')))
  vi.stubEnv('HERMES_HOME', home)
  resetUiState()
  patchUiState({ sid: 'owner' })
  let queue!: ReturnType<typeof useQueue>

  function Harness() {
    queue = useQueue()

    return <Text>{queue.queuedDisplay.join('|') || 'empty'}</Text>
  }

  const renderOptions = {
    patchConsole: false,
    stdin: new PassThrough() as any,
    stdout: Object.assign(new PassThrough(), { columns: 80, rows: 20, isTTY: false }) as any,
    stderr: new PassThrough() as any
  }
  let instance = renderSync(<Harness />, renderOptions)

  return {
    home,
    get queue() { return queue },
    remount() {
      instance.unmount()
      instance = renderSync(<Harness />, renderOptions)
    },
    close() {
      instance.unmount()
      instance.cleanup()
      resetUiState()
      vi.unstubAllEnvs()
      clearFsGate()

      try { chmodSync(join(home, 'tui-pending-inputs'), 0o700) } catch { /* not created */ }

      rmSync(home, { recursive: true, force: true })
    }
  }
}

function mountSubmission() {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'ink-queue-replace-submit-')))
  vi.stubEnv('HERMES_HOME', home)
  vi.stubEnv('HERMES_TUI_GATEWAY_URL', '')
  resetUiState()
  patchUiState({ sid: 'owner' })
  const request = vi.fn((method: string, params: any) => {
    if (method === 'prompt.submit') {
      return Promise.resolve({ admission_id: params.submission_id, input_id: params.submission_id, status: 'started' })
    }

    return Promise.resolve({})
  })
  const gw = { request, isCanonical: true } as any
  const submitRef = { current: (_value: string) => {} }
  const slashRef = { current: (_value: string) => false }
  const sys = vi.fn()
  let composer!: ReturnType<typeof useComposerState>
  let submission!: ReturnType<typeof useSubmission>

  function Harness() {
    composer = useComposerState({ gw, submitRef, sys })
    submission = useSubmission({
      gw, submitRef, slashRef, sys,
      composerActions: composer.actions,
      composerRefs: composer.refs,
      composerState: composer.state,
      appendMessage: vi.fn(),
      setLastUserMsg: vi.fn()
    })

    return <Text>{composer.state.input}</Text>
  }

  const instance = renderSync(<Harness />, {
    patchConsole: false,
    stdin: new PassThrough() as any,
    stdout: Object.assign(new PassThrough(), { columns: 80, rows: 20, isTTY: false }) as any,
    stderr: new PassThrough() as any
  })

  return {
    home, request,
    get composer() { return composer },
    async submit(command: string) {
      composer.actions.setInput(command)
      await new Promise<void>(resolve => setImmediate(resolve))
      submission.submit(command)
      await new Promise<void>(resolve => setImmediate(resolve))
    },
    close() {
      instance.unmount()
      instance.cleanup()
      resetUiState()
      vi.unstubAllEnvs()
      clearFsGate()
      const journal = join(home, 'tui-pending-inputs')

      try { chmodSync(journal, 0o700) } catch { /* directory may not exist */ }

      rmSync(home, { recursive: true, force: true })
    }
  }
}

afterEach(clearFsGate)

function enqueueAttachment(queue: ReturnType<typeof useQueue>): QueueItem {
  return queue.enqueue('look at this', 'look at this', undefined, attachments)!
}

it('an ENOSPC write during an edited takeQ leaves the old journal and does not unlink it', () => {
  const h = mountQueue()

  try {
    const queued = enqueueAttachment(h.queue)
    const removed: string[] = []
    fsGate.remove = path => { removed.push(path) }
    fsGate.write = path => {
      if (path.includes('tui-pending-inputs') && path.endsWith('.tmp')) {
        throw fsError('ENOSPC', 'write', path)
      }
    }

    expect(() => h.queue.takeQ(0, 'revised caption')).toThrow(expect.objectContaining({ code: 'ENOSPC' }))
    expect(removed).toEqual([])
    expect(h.queue.queueRef.current).toHaveLength(1)
    expect(h.queue.queueRef.current[0]).toMatchObject({ submissionId: queued.submissionId, text: 'look at this' })
    expect(h.queue.queueRef.current[0]!.inFlight).toBeFalsy()
    expect(journalRecords(h.home)).toEqual([
      expect.objectContaining({ submissionId: queued.submissionId, text: 'look at this', attachments })
    ])
    expect(journalRecords(h.home)[0].attempted).toBeFalsy()

    clearFsGate()
    const claimed = h.queue.takeQ(0, 'revised caption')
    expect(claimed).toMatchObject({ text: 'revised caption', inFlight: true, attachments })
    expect(claimed!.submissionId).not.toBe(queued.submissionId)
    expect(h.queue.queueRef.current.filter(item => item.text === 'revised caption')).toHaveLength(1)
    expect(h.queue.dequeue()).toBeUndefined()
    expect(h.queue.takeQ(0, 'revised again')).toBeUndefined()
    expect(journalRecords(h.home)).toEqual([
      expect.objectContaining({
        submissionId: claimed!.submissionId,
        text: 'revised caption',
        attempted: true,
        attachments
      })
    ])
  } finally { h.close() }
})

it('an ENOSPC rename during an edited takeQ does not publish a second journal', () => {
  const h = mountQueue()

  try {
    const queued = enqueueAttachment(h.queue)
    fsGate.rename = (_from, to) => {
      if (to.includes('tui-pending-inputs') && to.endsWith('.json') && !to.endsWith(`${queued.submissionId}.json`)) {
        throw fsError('ENOSPC', 'rename', to)
      }
    }

    expect(() => h.queue.takeQ(0, 'revised caption')).toThrow(expect.objectContaining({ code: 'ENOSPC' }))
    expect(h.queue.queueRef.current.map(item => item.submissionId)).toEqual([queued.submissionId])
    expect(h.queue.queueRef.current[0]!.inFlight).toBeFalsy()
    expect(journalRecords(h.home).map(row => row.submissionId)).toEqual([queued.submissionId])
    expect(journalRecords(h.home)[0].attachments).toEqual(attachments)
  } finally { h.close() }
})

it('keeps both journals attempted when deleting the old record fails, so restart does not auto-dispatch', () => {
  const h = mountQueue()

  try {
    const queued = enqueueAttachment(h.queue)
    fsGate.remove = path => {
      if (path.endsWith(`${queued.submissionId}.json`)) {
        throw fsError('EACCES', 'unlink', path)
      }
    }

    const claimed = h.queue.takeQ(0, 'revised caption')
    expect(claimed).toMatchObject({ text: 'revised caption', inFlight: true, attachments })
    expect(getUiState().status).toContain('journal cleanup failed')
    expect(h.queue.queueRef.current).toHaveLength(1)
    expect(h.queue.dequeue()).toBeUndefined()
    const rows = journalRecords(h.home)
    expect(rows).toHaveLength(2)
    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ submissionId: queued.submissionId, text: 'look at this', attempted: true, attachments }),
      expect.objectContaining({ submissionId: claimed!.submissionId, text: 'revised caption', attempted: true, attachments })
    ]))

    clearFsGate()
    h.remount()
    expect(h.queue.dequeue()).toBeUndefined()
    expect(h.queue.queueRef.current.map(item => item.submissionId).sort()).toEqual(
      [queued.submissionId, claimed!.submissionId].sort()
    )
    expect(h.queue.queueRef.current.every(item => item.failed)).toBe(true)
    const retried = h.queue.dequeue(true)
    expect([queued.submissionId, claimed!.submissionId]).toContain(retried?.submissionId)
    expect(retried?.inFlight).toBe(true)
    expect(h.queue.dequeue()).toBeUndefined()
    expect(h.queue.queueRef.current.filter(item => item.inFlight)).toHaveLength(1)
  } finally { h.close() }
})

it('reentry during the replacement claim does not remove the old journal or claim a second id', () => {
  const h = mountQueue()
  let oldId = ''
  const real = pendingInputs.savePendingInput
  // Calls through to the real write. The spy only injects same-index reentry.
  const save = vi.spyOn(pendingInputs, 'savePendingInput').mockImplementation(item => {
    if (item.inFlight && item.submissionId !== oldId) {
      expect(h.queue.takeQ(0, 'nested edit')).toBeUndefined()
      expect(h.queue.dequeue()).toBeUndefined()
      expect(journalRecords(h.home).map(row => row.submissionId)).toEqual([oldId])
    }

    return real(item)
  })

  try {
    oldId = enqueueAttachment(h.queue).submissionId!
    const claimed = h.queue.takeQ(0, 'revised caption')
    expect(claimed).toMatchObject({ text: 'revised caption', inFlight: true, attachments })
    expect(claimed!.submissionId).not.toBe(oldId)
    expect(journalRecords(h.home)).toEqual([
      expect.objectContaining({ submissionId: claimed!.submissionId, attachments, attempted: true })
    ])
  } finally { save.mockRestore(); h.close() }
})

it('a non-writable journal directory fails the edited takeQ with EACCES and keeps the old attachment', () => {
  const h = mountQueue()
  const journal = join(h.home, 'tui-pending-inputs')

  try {
    const queued = enqueueAttachment(h.queue)
    chmodSync(journal, 0o500)

    expect(() => h.queue.takeQ(0, 'revised caption')).toThrow(expect.objectContaining({ code: 'EACCES' }))
    expect(h.queue.queueRef.current).toEqual([
      expect.objectContaining({ submissionId: queued.submissionId, text: 'look at this' })
    ])
    expect(h.queue.queueRef.current[0]!.inFlight).toBeFalsy()
    expect(journalRecords(h.home)).toEqual([
      expect.objectContaining({ submissionId: queued.submissionId, attachments })
    ])

    chmodSync(journal, 0o700)
    const claimed = h.queue.takeQ(0, 'revised caption')
    expect(claimed).toMatchObject({ text: 'revised caption', inFlight: true, attachments })
    expect(claimed!.submissionId).not.toBe(queued.submissionId)
    expect(journalRecords(h.home).map(row => row.submissionId)).toEqual([claimed!.submissionId])
    expect(h.queue.takeQ(0, 'revised again')).toBeUndefined()
  } finally { h.close() }
})

it('an edited submission keeps composer tokens and the old attachment when the journal directory is not writable, then dispatches once', async () => {
  const h = mountSubmission()
  const path = join(h.home, 'shot.png')
  writeFileSync(path, png)
  const token = { kind: 'image' as const, index: 1, label: '[[ Image 1 ]]', mime: 'image/png', path }
  const journal = join(h.home, 'tui-pending-inputs')

  try {
    const queued = h.composer.actions.enqueue('look at this', 'look at this', undefined, [{ mime: 'image/png', path }])!
    h.composer.actions.setQueueEdit(0)
    h.composer.actions.setComposerTokens([token])
    chmodSync(journal, 0o500)
    await h.submit('look at this closely [[ Image 1 ]]')
    expect(h.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(0)
    expect(h.composer.state.input).toBe('look at this closely [[ Image 1 ]]')
    expect(h.composer.refs.tokensRef.current).toEqual([expect.objectContaining({ kind: 'image', mime: 'image/png', path })])
    expect(h.composer.refs.queueRef.current).toHaveLength(1)
    expect(h.composer.refs.queueRef.current[0]).toMatchObject({ submissionId: queued.submissionId, text: 'look at this' })
    expect(h.composer.refs.queueRef.current[0]!.inFlight).toBeFalsy()
    expect(getUiState().status).toBe('input not saved')
    expect(journalRecords(h.home)).toEqual([
      expect.objectContaining({
        submissionId: queued.submissionId,
        attachments: [expect.objectContaining({ mime: 'image/png', path })]
      })
    ])

    chmodSync(journal, 0o700)
    h.composer.actions.setQueueEdit(0)
    await h.submit('look at this closely [[ Image 1 ]]')
    await vi.waitFor(() => expect(h.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1))
    expect(h.request.mock.calls.find(([method]) => method === 'prompt.submit')![1].attachments).toHaveLength(1)
    expect(journalRecords(h.home).map(row => row.submissionId)).not.toContain(queued.submissionId)
    expect(h.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
  } finally { h.close() }
})
