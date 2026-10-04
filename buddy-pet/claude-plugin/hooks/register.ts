import type { EngineInterface, Register } from 'claude-code'

// 桌宠Claude娘读的目录是 <用户目录>/.claude/buddy-pet/live，每个会话一个 <会话 id>.json。
// 位置固定，桌宠装在哪里、是否打包成 exe 都按这个路径找。文件里只有额度百分比、重置时间、
// 上下文 token、花费和任务的起止时间，不写提示词、回答或项目路径。
const HEARTBEAT_MS = 60_000

type Limit = { kind: string; percentUsed: number; resetsAt?: string }
type Measure = {
  context: { tokens?: number; window: number; percent?: number }
  rateLimits: readonly Limit[]
  cost?: { usd: number }
}
type Turn = {
  state: 'busy' | 'idle'
  seq: number
  startedAt?: number
  endedAt?: number
  reason?: string
  durationMs?: number
}

// 模块重新加载时这些都从头开始，下一次 session.measure 会补齐。
const live = {
  folder: undefined as string | undefined,
  startedAt: undefined as number | undefined,
  context: undefined as Measure['context'] | undefined,
  cost: undefined as number | undefined,
  measuredAt: 0,
  // 第一次响应之前（还有重新加载、换模型、恢复会话之后）引擎报不出额度，这时沿用上一次的读数。
  limits: [] as Limit[],
  limitsAt: 0,
  turn: { state: 'idle', seq: 0 } as Turn,
  isTicking: false,
  isWarned: false,
  queue: Promise.resolve(),
}

async function locate($: EngineInterface): Promise<string | undefined> {
  if (live.folder === undefined) {
    const home = (await $.env.get('USERPROFILE')) || (await $.env.get('HOME'))
    if (!home) return undefined
    const sep = home.includes('\\') ? '\\' : '/'
    live.folder = [home.replace(/[\\/]+$/, ''), '.claude', 'buddy-pet', 'live'].join(sep)
  }

  return live.folder
}

function take(m: Measure, now: number): void {
  live.context = { tokens: m.context.tokens, window: m.context.window, percent: m.context.percent }
  if (m.cost) live.cost = m.cost.usd
  if (m.rateLimits.length) {
    live.limits = m.rateLimits.map(l => ({ kind: l.kind, percentUsed: l.percentUsed, resetsAt: l.resetsAt }))
    live.limitsAt = now
  }
  live.measuredAt = now
}

async function write($: EngineInterface, isEnded: boolean): Promise<void> {
  const dir = await locate($)
  if (dir === undefined) return
  // 会话 id 每次重新取：/clear 之后进程换一个 id 继续跑，自动写到新文件。
  const session = await $.session.id()
  const body = {
    v: 1,
    session,
    updated: await $.clock.now(),
    startedAt: live.startedAt,
    limits: live.limits,
    limitsAt: live.limitsAt,
    context: live.context,
    cost: live.cost,
    measuredAt: live.measuredAt,
    turn: live.turn,
    ended: isEnded,
  }
  await $.fs.write(`${dir}${dir.includes('\\') ? '\\' : '/'}${session}.json`, JSON.stringify(body))
}

// 写入排队进行，每次写整份内容，后一次覆盖前一次；写不进去只提示一次。
function save($: EngineInterface, isEnded = false): Promise<void> {
  live.queue = live.queue
    .then(() => write($, isEnded))
    .catch((err: unknown) => {
      if (live.isWarned) return
      live.isWarned = true
      $.ui.toast(`claude-buddy-bridge 写不进桌宠的数据文件：${err instanceof Error ? err.message : String(err)}`)
    })

  return live.queue
}

function beat($: EngineInterface): void {
  if (live.isTicking) return
  live.isTicking = true
  // 心跳：桌宠据此判断会话还在；超过三分钟没更新的会话，她就不再当它在干活。
  $.clock.every(HEARTBEAT_MS, () => {
    void save($)
  })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const usage = await $.session.usage()
    live.startedAt = usage.startedAt
    take(usage, await $.clock.now())
    void save($)
    beat($)

    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    take(e, await $.clock.now())
    void save($)

    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    live.turn = { state: 'busy', seq: live.turn.seq + 1, startedAt: await $.clock.now() }
    void save($)

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // 子代理的回合只是主回合里的一步，不算任务结束。
    if (e.agentId) return next(e)
    live.turn = { ...live.turn, state: 'idle', endedAt: await $.clock.now(), reason: e.reason, durationMs: e.durationMs }
    void save($)

    return next(e)
  })

  on('session.end', async ($, e, next) => {
    if (live.turn.state === 'busy') {
      live.turn = { ...live.turn, state: 'idle', endedAt: await $.clock.now(), reason: 'ended' }
    }
    await save($, true)
    // /clear 之后进程换一个会话 id 继续跑，新会话从零算起。
    live.context = undefined
    live.cost = undefined
    live.startedAt = await $.clock.now()

    return next(e)
  })
}
