// PROTOTYPE, throwaway. Stub hub with four instances. Turn records are generated, then bucketed
// the way stats.get does it (UTC day or Monday week), plus the proposed per-origin breakdown.

export type UsageSource = "api" | "claude-subscription" | "chatgpt-subscription"
export type SubscriptionSource = Exclude<UsageSource, "api">
export type ClosingKind = "completed" | "failed" | "interrupted"
export type Origin = { kind: "user" } | { kind: "routine"; name: string; trigger: string }

export const NOW = new Date("2026-09-26T16:20:00Z")
const HOUR = 3_600_000
const DAY = 24 * HOUR

export const SOURCE_LABEL: Record<UsageSource, string> = {
  api: "API",
  "claude-subscription": "Claude plan",
  "chatgpt-subscription": "ChatGPT plan",
}

export interface DelegatedRun {
  usage_source: SubscriptionSource
  client: string
  outcome: "completed" | "failed" | "limited"
  input_tokens: number
  output_tokens: number
  duration_ms: number
  at: Date
  resets_at?: Date
}

export interface TurnRecord {
  turn_id: string
  thread_title: string
  origin: Origin
  closed_at: Date
  closing_kind: ClosingKind
  model: string
  input_tokens: number
  output_tokens: number
  cost: number | null
  rating: "good" | "bad" | null
  tool_calls: Record<string, number>
  memory_calls: { search: number; open: number; remember: number; forget: number }
  approvals_requested: number
  denies: number
  reads_before_first_write: number
  tokens_before_first_write: number
  delegated_runs: DelegatedRun[]
}

export interface SubscriptionUse {
  usage_source: SubscriptionSource
  runs: number
  input_tokens: number
  output_tokens: number
  duration_ms: number
}

export interface OriginUse {
  /** "chat", or the routine's name. */
  origin: string
  turns: number
  failed: number
  cost: number | null
  subscription_runs: number
}

export interface StatsSummary {
  completed: number
  failed: number
  interrupted: number
  input_tokens: number
  output_tokens: number
  cost: number | null
  tool_calls: Record<string, number>
  memory_calls: { search: number; open: number; remember: number; forget: number }
  turns_without_memory: number
  approvals_requested: number
  denies: number
  good_ratings: number
  bad_ratings: number
  navigation: { turns: number; read_calls: number | null; tokens_before_first_write: number | null }
  subscriptions: SubscriptionUse[]
  by_origin: OriginUse[]
}

export interface StatsBucket extends StatsSummary {
  start: string
}

export interface PlanLimit {
  usage_source: SubscriptionSource
  resets_at: Date
}

export interface RoutineSummary {
  name: string
  schedule: string | null
  enabled: boolean
  failure_count: number
  last_run: { outcome: string; started_at: Date } | null
  next_run: Date | null
}

export interface Instance {
  id: string
  name: string
  package: string | null
  state: "running" | "stopped" | "unreachable"
  records: TurnRecord[]
  routines: RoutineSummary[]
  unpriced_models: string[]
  mismatches: number
}

export type Range = 7 | 30 | 90
export type BucketSize = "day" | "week"

export const bucketSize = (range: Range): BucketSize => (range === 90 ? "week" : "day")
export const since = (range: Range) => new Date(NOW.getTime() - range * DAY)

// ---- generation ----------------------------------------------------------------------------

function rng(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

interface Profile {
  seed: number
  chatPerDay: number
  routines: { name: string; trigger: string; perDay: number; delegates: boolean }[]
  models: { name: string; priced: boolean }[]
  learns: boolean
}

const CHAT_TITLES = [
  "Plan the hub release",
  "Why is the relay dropping frames",
  "Summarize this week's PRs",
  "Draft reply to Ana",
  "Fix flaky hub test",
  "Rename usage source columns",
  "What changed in the contract",
  "Book the dentist",
]

function generate(profile: Profile): TurnRecord[] {
  const r = rng(profile.seed)
  const records: TurnRecord[] = []
  let n = 0
  for (let day = 95; day >= 0; day--) {
    const dayStart = Math.floor(NOW.getTime() / DAY) * DAY - day * DAY
    const weekend = [0, 6].includes(new Date(dayStart).getUTCDay())
    const origins: Origin[] = []
    const chats = Math.round(profile.chatPerDay * (weekend ? 0.3 : 1) * (0.4 + r() * 1.2))
    for (let i = 0; i < chats; i++) origins.push({ kind: "user" })
    for (const routine of profile.routines) {
      const count = Math.round(routine.perDay * (0.5 + r()))
      for (let i = 0; i < count; i++)
        origins.push({ kind: "routine", name: routine.name, trigger: routine.trigger })
    }
    for (const origin of origins) {
      const closed = new Date(dayStart + r() * DAY)
      if (closed > NOW) continue
      const routine = origin.kind === "routine" ? profile.routines.find((x) => x.name === origin.name) : undefined
      const roll = r()
      const closing_kind: ClosingKind = roll < 0.06 ? "failed" : roll < 0.08 ? "interrupted" : "completed"
      const model = profile.models[Math.floor(r() * profile.models.length)]
      const input = Math.round(4_000 + r() * (origin.kind === "user" ? 60_000 : 25_000))
      const output = Math.round(300 + r() * 4_000)
      // Navigation shrinks over time when memory teaches the workspace.
      const learning = profile.learns ? 0.35 + (day / 95) * 0.9 : 1
      const reads = Math.round((3 + r() * 16) * learning)
      const delegated: DelegatedRun[] = []
      if (routine?.delegates && closing_kind !== "interrupted") {
        const runs = 1 + Math.floor(r() * 3)
        for (let i = 0; i < runs; i++) {
          const claude = r() < 0.4
          delegated.push({
            usage_source: claude ? "claude-subscription" : "chatgpt-subscription",
            client: claude ? "claude-code" : "codex",
            outcome: r() < 0.05 ? "failed" : "completed",
            input_tokens: Math.round(40_000 + r() * 400_000),
            output_tokens: Math.round(2_000 + r() * 30_000),
            duration_ms: Math.round((2 + r() * 25) * 60_000),
            at: closed,
          })
        }
      }
      const memoryUsed = r() < 0.7
      records.push({
        turn_id: `t-${profile.seed}-${n++}`,
        thread_title:
          origin.kind === "user"
            ? CHAT_TITLES[Math.floor(r() * CHAT_TITLES.length)]
            : `${origin.name} · ${closed.toISOString().slice(5, 16).replace("T", " ")}`,
        origin,
        closed_at: closed,
        closing_kind,
        model: model.name,
        input_tokens: input,
        output_tokens: output,
        cost: model.priced ? (input * 3 + output * 15) / 1_000_000 : null,
        rating: origin.kind === "user" && r() < 0.3 ? (r() < 0.8 ? "good" : "bad") : null,
        tool_calls: {
          read_file: Math.round(reads * 0.6),
          search: Math.round(reads * 0.4),
          write_file: Math.round(r() * 4),
          shell: Math.round(r() * 3),
          ...(routine?.delegates ? { code_step: delegated.length } : {}),
        },
        memory_calls: memoryUsed
          ? {
              search: 1 + Math.floor(r() * 3),
              open: Math.floor(r() * 3),
              remember: r() < 0.3 ? 1 : 0,
              forget: r() < 0.03 ? 1 : 0,
            }
          : { search: 0, open: 0, remember: 0, forget: 0 },
        approvals_requested: r() < 0.15 ? 1 : 0,
        denies: r() < 0.03 ? 1 : 0,
        reads_before_first_write: reads,
        tokens_before_first_write: Math.round(reads * (1_200 + r() * 800)),
        delegated_runs: delegated,
      })
    }
  }
  // The latest Claude Code run hit the plan's limit.
  const last = [...records].reverse().find((t) => t.delegated_runs.some((d) => d.usage_source === "claude-subscription"))
  const run = last?.delegated_runs.find((d) => d.usage_source === "claude-subscription")
  if (profile.seed === 1 && run) {
    run.outcome = "limited"
    run.resets_at = new Date("2026-09-26T18:40:00Z")
  }
  return records.sort((a, b) => a.closed_at.getTime() - b.closed_at.getTime())
}

const CODER = generate({
  seed: 1,
  chatPerDay: 3,
  routines: [
    { name: "pick-ready-issues", trigger: "scheduled", perDay: 4, delegates: true },
    { name: "babysit-prs", trigger: "scheduled", perDay: 5, delegates: true },
    { name: "github-webhook", trigger: "signal", perDay: 1.5, delegates: false },
  ],
  models: [{ name: "claude-sonnet-5", priced: true }],
  learns: true,
})

const MATE = generate({
  seed: 2,
  chatPerDay: 6,
  routines: [
    { name: "morning-brief", trigger: "scheduled", perDay: 1, delegates: false },
    { name: "inbox", trigger: "signal", perDay: 3, delegates: false },
  ],
  models: [
    { name: "claude-sonnet-5", priced: true },
    { name: "local/qwen3-32b", priced: false },
  ],
  learns: false,
})

const SCRIBE = generate({
  seed: 3,
  chatPerDay: 2,
  routines: [{ name: "weekly-post", trigger: "scheduled", perDay: 0.2, delegates: false }],
  models: [{ name: "claude-sonnet-5", priced: true }],
  learns: false,
})

export const INSTANCES: Instance[] = [
  {
    id: "coder",
    name: "Coder",
    package: "software factory",
    state: "running",
    records: CODER,
    unpriced_models: [],
    mismatches: 1,
    routines: [
      {
        name: "pick-ready-issues",
        schedule: "*/30 * * * *",
        enabled: true,
        failure_count: 0,
        last_run: { outcome: "work", started_at: new Date(NOW.getTime() - 0.3 * HOUR) },
        next_run: new Date(NOW.getTime() + 0.2 * HOUR),
      },
      {
        name: "babysit-prs",
        schedule: "*/15 * * * *",
        enabled: true,
        failure_count: 2,
        last_run: { outcome: "failed", started_at: new Date(NOW.getTime() - 0.1 * HOUR) },
        next_run: new Date(NOW.getTime() + 0.15 * HOUR),
      },
      {
        name: "github-webhook",
        schedule: null,
        enabled: true,
        failure_count: 0,
        last_run: { outcome: "no-work", started_at: new Date(NOW.getTime() - 2 * HOUR) },
        next_run: null,
      },
    ],
  },
  {
    id: "mate",
    name: "Life mate",
    package: "life mate",
    state: "running",
    records: MATE,
    unpriced_models: ["local/qwen3-32b"],
    mismatches: 0,
    routines: [
      {
        name: "morning-brief",
        schedule: "0 7 * * 1-5",
        enabled: true,
        failure_count: 0,
        last_run: { outcome: "work", started_at: new Date("2026-09-25T07:00:00Z") },
        next_run: new Date("2026-09-28T07:00:00Z"),
      },
      {
        name: "inbox",
        schedule: null,
        enabled: true,
        failure_count: 0,
        last_run: { outcome: "work", started_at: new Date(NOW.getTime() - 1.4 * HOUR) },
        next_run: null,
      },
    ],
  },
  {
    id: "scribe",
    name: "Scribe",
    package: null,
    state: "stopped",
    records: SCRIBE,
    unpriced_models: [],
    mismatches: 0,
    routines: [],
  },
  {
    id: "scout",
    name: "Scout",
    package: null,
    state: "unreachable",
    records: [],
    unpriced_models: [],
    mismatches: 0,
    routines: [],
  },
]

// ---- stats.get, as the instance would answer ----------------------------------------------

function bucketStart(date: Date, by: BucketSize): string {
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  if (by === "week") day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7))
  return day.toISOString().slice(0, 10)
}

function originKey(origin: Origin) {
  return origin.kind === "user" ? "chat" : origin.name
}

function summarize(records: TurnRecord[]): StatsSummary {
  const sum = (f: (t: TurnRecord) => number) => records.reduce((acc, t) => acc + f(t), 0)
  const priced = records.filter((t) => t.cost !== null)
  const tools: Record<string, number> = {}
  for (const t of records) for (const [k, v] of Object.entries(t.tool_calls)) tools[k] = (tools[k] ?? 0) + v
  const subscriptions = (["claude-subscription", "chatgpt-subscription"] as const).map((source) => {
    const runs = records.flatMap((t) => t.delegated_runs).filter((d) => d.usage_source === source)
    return {
      usage_source: source,
      runs: runs.length,
      input_tokens: runs.reduce((a, d) => a + d.input_tokens, 0),
      output_tokens: runs.reduce((a, d) => a + d.output_tokens, 0),
      duration_ms: runs.reduce((a, d) => a + d.duration_ms, 0),
    }
  })
  const origins = new Map<string, TurnRecord[]>()
  for (const t of records) origins.set(originKey(t.origin), [...(origins.get(originKey(t.origin)) ?? []), t])
  const navigated = records.filter((t) => t.reads_before_first_write > 0)
  return {
    completed: records.filter((t) => t.closing_kind === "completed").length,
    failed: records.filter((t) => t.closing_kind === "failed").length,
    interrupted: records.filter((t) => t.closing_kind === "interrupted").length,
    input_tokens: sum((t) => t.input_tokens),
    output_tokens: sum((t) => t.output_tokens),
    cost: priced.length ? priced.reduce((a, t) => a + (t.cost ?? 0), 0) : null,
    tool_calls: tools,
    memory_calls: {
      search: sum((t) => t.memory_calls.search),
      open: sum((t) => t.memory_calls.open),
      remember: sum((t) => t.memory_calls.remember),
      forget: sum((t) => t.memory_calls.forget),
    },
    turns_without_memory: records.filter((t) => t.memory_calls.search + t.memory_calls.open === 0).length,
    approvals_requested: sum((t) => t.approvals_requested),
    denies: sum((t) => t.denies),
    good_ratings: records.filter((t) => t.rating === "good").length,
    bad_ratings: records.filter((t) => t.rating === "bad").length,
    navigation: {
      turns: navigated.length,
      read_calls: navigated.length ? navigated.reduce((a, t) => a + t.reads_before_first_write, 0) / navigated.length : null,
      tokens_before_first_write: navigated.length
        ? navigated.reduce((a, t) => a + t.tokens_before_first_write, 0) / navigated.length
        : null,
    },
    subscriptions,
    by_origin: [...origins.entries()]
      .map(([origin, turns]) => {
        const p = turns.filter((t) => t.cost !== null)
        return {
          origin,
          turns: turns.length,
          failed: turns.filter((t) => t.closing_kind === "failed").length,
          cost: p.length ? p.reduce((a, t) => a + (t.cost ?? 0), 0) : null,
          subscription_runs: turns.reduce((a, t) => a + t.delegated_runs.length, 0),
        }
      })
      .sort((a, b) => (a.origin === "chat" ? -1 : b.origin === "chat" ? 1 : b.turns - a.turns)),
  }
}

export interface StatsGetResult {
  records: TurnRecord[]
  buckets: StatsBucket[]
  total: StatsSummary
  limits: PlanLimit[]
  unpriced_models: string[]
  mismatches: number
}

export function statsGet(instance: Instance, from: Date, by: BucketSize): StatsGetResult {
  const records = instance.records.filter((t) => t.closed_at >= from && t.closed_at <= NOW)
  const groups = new Map<string, TurnRecord[]>()
  for (let d = new Date(from); d <= NOW; d = new Date(d.getTime() + DAY)) groups.set(bucketStart(d, by), [])
  for (const t of records) groups.get(bucketStart(t.closed_at, by))?.push(t)
  const limits = instance.records
    .flatMap((t) => t.delegated_runs)
    .filter((d) => d.outcome === "limited" && d.resets_at && d.resets_at > NOW)
    .map((d) => ({ usage_source: d.usage_source, resets_at: d.resets_at as Date }))
  return {
    records,
    buckets: [...groups.entries()].map(([start, turns]) => ({ start, ...summarize(turns) })),
    total: summarize(records),
    limits,
    unpriced_models: instance.unpriced_models,
    mismatches: instance.mismatches,
  }
}

// ---- stats.summary, as the hub would answer --------------------------------------------------

export interface StatsSummaryResult {
  buckets: Record<string, StatsBucket[]>
  totals: Record<string, StatsSummary>
  api: { input_tokens: number; output_tokens: number; cost: number | null }
  subscriptions: SubscriptionUse[]
  limits: PlanLimit[]
  skipped: string[]
  unreachable: string[]
}

export function statsSummary(from: Date, by: BucketSize): StatsSummaryResult {
  const counted = INSTANCES.filter((i) => i.state === "running")
  const results = Object.fromEntries(counted.map((i) => [i.id, statsGet(i, from, by)]))
  const totals = Object.fromEntries(counted.map((i) => [i.id, results[i.id].total]))
  const all = Object.values(totals)
  const costs = all.map((t) => t.cost).filter((c): c is number => c !== null)
  return {
    buckets: Object.fromEntries(counted.map((i) => [i.id, results[i.id].buckets])),
    totals,
    api: {
      input_tokens: all.reduce((a, t) => a + t.input_tokens, 0),
      output_tokens: all.reduce((a, t) => a + t.output_tokens, 0),
      cost: costs.length ? costs.reduce((a, c) => a + c, 0) : null,
    },
    subscriptions: (["claude-subscription", "chatgpt-subscription"] as const).map((source) => {
      const uses = all.flatMap((t) => t.subscriptions).filter((s) => s.usage_source === source)
      return {
        usage_source: source,
        runs: uses.reduce((a, s) => a + s.runs, 0),
        input_tokens: uses.reduce((a, s) => a + s.input_tokens, 0),
        output_tokens: uses.reduce((a, s) => a + s.output_tokens, 0),
        duration_ms: uses.reduce((a, s) => a + s.duration_ms, 0),
      }
    }),
    limits: Object.values(results).flatMap((r) => r.limits),
    skipped: INSTANCES.filter((i) => i.state === "stopped").map((i) => i.id),
    unreachable: INSTANCES.filter((i) => i.state === "unreachable").map((i) => i.id),
  }
}

/** Subscription use over the last five hours and seven days: two more reads with `since = now − window`. */
export function planWindows(get: (from: Date) => SubscriptionUse[]) {
  return {
    fiveHours: get(new Date(NOW.getTime() - 5 * HOUR)),
    sevenDays: get(new Date(NOW.getTime() - 7 * DAY)),
  }
}

export const instanceName = (id: string) => INSTANCES.find((i) => i.id === id)?.name ?? id
