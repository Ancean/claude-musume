import { expect, mock, test } from 'claude-code/testing'

const HOME = 'C:\\Users\\tester'
const FILE = `${HOME}\\.claude\\buddy-pet\\live\\s1.json`
const T0 = 1_700_000_000_000

test('会话文件跟着额度、回合和心跳更新', async ($, on) => {
  const clock = mock.clock(on, { now: T0 })
  mock.env(on, { USERPROFILE: HOME })
  const files = new Map<string, string>()
  on('fs.write', (_$, e) => {
    files.set(e.path, e.text)

    return { value: undefined }
  })
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: T0 - 60_000, context: { window: 200_000 }, rateLimits: [] } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.measure', (_$, e) => ({ changed: e.changed }))
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('session.end', (_$, e) => ({ sessionId: e.sessionId }))
  const read = () => JSON.parse(files.get(FILE) ?? 'null')

  await $.session.start({ cwd: 'D:\\proj', surface: null, isInteractive: false })
  await clock.settle()
  expect(read()).toMatchObject({ v: 1, session: 's1', startedAt: T0 - 60_000, limits: [], ended: false })
  expect(read().turn).toEqual({ state: 'idle', seq: 0 })

  await $.session.measure({
    context: { tokens: 32_000, window: 200_000, percent: 16 },
    rateLimits: [{ kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-04T12:00:00Z' }],
    cost: { usd: 1.25 },
    changed: ['context', 'rateLimits', 'cost'],
  })
  await clock.settle()
  expect(read()).toMatchObject({ limitsAt: T0, measuredAt: T0, cost: 1.25, context: { tokens: 32_000, window: 200_000 } })
  expect(read().limits).toEqual([{ kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-04T12:00:00Z' }])

  await $.turn.start({ text: '整理一下', turnId: 't1' })
  await clock.settle()
  expect(read().turn).toEqual({ state: 'busy', seq: 1, startedAt: T0 })

  // 回合进行中，心跳每分钟重写一次。
  await clock.advance(90_000)
  expect(read().updated).toBe(T0 + 60_000)
  expect(read().turn.state).toBe('busy')

  await $.turn.complete({ answer: '好了', durationMs: 90_000, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.settle()
  expect(read().turn).toEqual({ state: 'idle', seq: 1, startedAt: T0, endedAt: T0 + 90_000, reason: 'answer', durationMs: 90_000 })

  // 子代理的回合结束不算任务结束。
  await $.turn.complete({ answer: '', durationMs: 5, isAborted: false, turnId: 't2', agentId: 'a1', reason: 'answer' })
  await clock.settle()
  expect(read().turn.durationMs).toBe(90_000)

  // 这一次响应没报额度时沿用上一次的读数和时间。
  await clock.advance(5_000)
  await $.session.measure({ context: { tokens: 40_000, window: 200_000, percent: 20 }, rateLimits: [], changed: ['context'] })
  await clock.settle()
  expect(read()).toMatchObject({ limitsAt: T0, measuredAt: T0 + 95_000, context: { tokens: 40_000 } })
  expect(read().limits).toHaveLength(1)

  await $.session.end({ reason: 'other', sessionId: 's1', resume: { id: 's1' } })
  expect(read().ended).toBe(true)
  expect(files.size).toBe(1)
  // 文件里只有数字和状态，没有提示词和回答。
  expect(files.get(FILE)).not.toContain('整理一下')
  expect(files.get(FILE)).not.toContain('好了')
})

test('写不进去时只提示一次', async ($, on) => {
  mock.clock(on, { now: T0 })
  mock.env(on, { USERPROFILE: HOME })
  const toasts: string[] = []
  on('fs.write', () => ({ deny: 'read-only' }))
  on('ui.toast', (_$, e) => {
    toasts.push(e.text)

    return { value: undefined }
  })
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: T0, context: { window: 200_000 }, rateLimits: [] } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))
  on('session.end', (_$, e) => ({ sessionId: e.sessionId }))

  await $.session.start({ cwd: 'D:\\proj', surface: null, isInteractive: false })
  await $.turn.start({ text: '', turnId: 't1' })
  await $.session.end({ reason: 'other', sessionId: 's1', resume: { id: 's1' } })
  expect(toasts).toHaveLength(1)
  expect(toasts[0]).toContain('claude-buddy-bridge')
})
