import type {
  ContextCategory,
  ContextRow,
  ManifestCompaction,
  OriginUse,
  StatsBucket,
  StatsBucketSize,
  StatsGetCommand,
  StatsGetResult,
  StatsSummary,
  TurnMetrics,
  UsageSource,
} from "@kinby/contract"

/** How many UTC days a range covers, today included. */
export type Range = 7 | 30 | 90

export const RANGES: readonly Range[] = [7, 30, 90]

const DAY_MS = 86_400_000

/** A quarter reads in weeks, so its chart stays readable. */
export function bucketSize(range: Range): StatsBucketSize {
  return range === 90 ? "week" : "day"
}

/** The stats.get for `range` days up to `now`: from midnight UTC on the first one, with no end. */
export function statsCommand(range: Range, now: Date): StatsGetCommand {
  return { since: firstDay(range, now).toISOString(), by: bucketSize(range) }
}

/** Midnight UTC on the first of `range` days up to `now`. */
function firstDay(range: Range, now: Date): Date {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return new Date(today - (range - 1) * DAY_MS)
}

/** The start of every bucket in `range` days up to `now`, first to last, as stats.get names them. */
export function bucketStarts(range: Range, by: StatsBucketSize, now: Date): string[] {
  const first = Date.parse(bucketStart(firstDay(range, now).toISOString(), by))
  const last = Date.parse(bucketStart(now.toISOString(), by))
  const step = by === "week" ? 7 * DAY_MS : DAY_MS
  return Array.from({ length: (last - first) / step + 1 }, (_, index) =>
    new Date(first + index * step).toISOString().slice(0, 10),
  )
}

/** What a bucket chart draws of a bucket. */
export type BucketPoint = Pick<
  StatsBucket,
  "start" | "completed" | "failed" | "interrupted" | "cost" | "subscriptions"
>

/**
 * A point per start: the bucket stats.get returned for it, or an empty one. An empty bucket's
 * cost is 0, not unpriced, since it holds no turn that could lack a price.
 */
export function bucketPoints(starts: string[], buckets: StatsBucket[]): BucketPoint[] {
  return starts.map(
    (start) =>
      buckets.find((bucket) => bucket.start === start) ?? {
        start,
        completed: 0,
        failed: 0,
        interrupted: 0,
        cost: 0,
        subscriptions: [],
      },
  )
}

/**
 * Whether a stats.get answer comes from a core older than the page, one without each plan's recent
 * runs or the turns by origin.
 */
export function fromOlderCore(answer: StatsGetResult): boolean {
  const read: { plan_use?: unknown; total: { origins?: unknown } } = answer
  return read.plan_use === undefined || read.total.origins === undefined
}

/** Every turn that closed, whatever way it closed. */
export function turnCount({ completed, failed, interrupted }: StatsSummary): number {
  return completed + failed + interrupted
}

export const SOURCE_LABELS: Record<UsageSource, string> = {
  api: "API",
  "claude-subscription": "Claude",
  "chatgpt-subscription": "ChatGPT",
}

/** An API cost in dollars, or "not priced" when no turn in it was priced, so it never reads as 0. */
export function money(cost: number | null | undefined): string {
  return cost === null || cost === undefined ? "not priced" : `$${cost.toFixed(2)}`
}

/** An API cost in cents. A chart plots cost in cents, so its whole-number ticks land on whole cents. */
export function cents(cost: number | null | undefined): number | null {
  return cost === null || cost === undefined ? null : cost * 100
}

/** A cost a chart plots in cents, written in dollars as `money` writes it. */
export function centsMoney(cents: number | null): string {
  return money(cents === null ? null : cents / 100)
}

/** A token count with thousands separators. */
export function tokens(count: number): string {
  return count.toLocaleString("en")
}

const SHORT = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 })

/** A token count in a quiet line, to one decimal of its thousands or millions: 9.1k, 1.2m. */
export function shortTokens(count: number): string {
  return SHORT.format(count).toLowerCase()
}

/**
 * A finished turn's quiet figures: its steps, its cost on API use or its largest context size where
 * nothing priced it, and its time.
 */
export function turnFigures({
  steps,
  cost,
  largestContext,
  durationMs,
}: {
  steps: number
  cost?: number
  largestContext?: number
  durationMs: number
}): string {
  const spend =
    cost !== undefined
      ? money(cost)
      : largestContext !== undefined
        ? `${shortTokens(largestContext)} context`
        : undefined
  return [`${steps} ${steps === 1 ? "step" : "steps"}`, spend, duration(durationMs)]
    .filter((figure) => figure !== undefined)
    .join(" · ")
}

/** How much of `limit` a context size fills, in percent. */
export function contextFill(contextSize: number, limit: number): number {
  return Math.min(100, (contextSize / limit) * 100)
}

/** What the effective compaction trigger leaves free of the model's window, in tokens. */
const WINDOW_RESERVE = 16_000

/**
 * The context size past which a thread compacts at its next turn's start: the instance's trigger,
 * or the model's window less the reserve when that is smaller. None with automatic compaction off,
 * or before the manifest is read.
 */
export function effectiveTrigger(
  compaction: ManifestCompaction | undefined,
  window: number | undefined,
): number | undefined {
  if (!compaction?.automatic) return undefined
  return window === undefined
    ? compaction.trigger
    : Math.min(compaction.trigger, window - WINDOW_RESERVE)
}

/**
 * What the context bar fills toward: the effective compaction trigger, or else the model's window.
 * A window no larger than the reserve leaves no trigger above zero, so the bar fills toward the window.
 */
export function contextLimit(
  compaction: ManifestCompaction | undefined,
  window: number | undefined,
): number | undefined {
  const trigger = effectiveTrigger(compaction, window)
  return trigger !== undefined && trigger > 0 ? trigger : window
}

/** A context size against `limit`, as the panel states it: "41k of 100k, 41%". */
export function contextAgainst(contextSize: number, limit: number): string {
  const share = Math.round((contextSize / limit) * 100)
  return `${shortTokens(contextSize)} of ${shortTokens(limit)}, ${share}%`
}

const CONTEXT_LABELS: Record<ContextCategory, string> = {
  system_prompt: "System prompt",
  profile: "Profile",
  skills_catalogue: "Skills catalogue",
  core_tools: "Core tools",
  authored_tools: "Authored tools",
  mcp_tools: "MCP tools",
  text: "User and model text",
  tool_results: "Tool results",
  skill_bodies: "Loaded skill bodies",
  compaction_summary: "Compaction summary",
}

/** The rows of a context size's breakdown, each with its share of the context size in percent. */
export function contextRows(
  rows: ContextRow[],
): { label: string; tokens: number; share: number }[] {
  const size = rows.reduce((sum, row) => sum + row.tokens, 0)
  return rows.map(({ category, tokens }) => ({
    label: CONTEXT_LABELS[category],
    tokens,
    share: size === 0 ? 0 : (tokens / size) * 100,
  }))
}

/** The middle of `values`, or the mean of the two middle ones. `values` holds at least one. */
export function median(values: number[]): number {
  const sorted = values.toSorted((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

/** A length of time as its two largest units: "1h 30m", "1m 35s", "40s". */
export function duration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  const [hours, minutes] = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60]
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`
  return `${seconds}s`
}

/** The bucket a turn closed in, named as stats.get names it: its UTC day, or the Monday of that week. */
function bucketStart(closedAt: string, by: StatsBucketSize): string {
  const closed = new Date(closedAt)
  const day = Date.UTC(closed.getUTCFullYear(), closed.getUTCMonth(), closed.getUTCDate())
  const monday = by === "week" ? ((closed.getUTCDay() + 6) % 7) * DAY_MS : 0
  return new Date(day - monday).toISOString().slice(0, 10)
}

/** What the drill-down lists: the turns of a clicked bar's bucket, of a clicked origin row, or both. */
export interface Drill {
  /** The start of the bucket whose bar was clicked. */
  bucket?: string
  origin?: OriginUse
}

/** The turns a drill picks, most expensive first and unpriced last. */
export function drilledTurns(
  records: TurnMetrics[],
  { bucket, origin }: Drill,
  by: StatsBucketSize,
): TurnMetrics[] {
  return records
    .filter((record) => bucket === undefined || bucketStart(record.closed_at, by) === bucket)
    .filter((record) => origin === undefined || originRoutine(record.origin) === origin.routine)
    .sort((a, b) => (b.cost ?? -1) - (a.cost ?? -1))
}

/** The routine a turn counts under in stats.get's origins, or null for chat. */
function originRoutine(origin: TurnMetrics["origin"]): string | null {
  return origin?.kind === "routine" ? origin.name : null
}

/** One bucket's point on the navigation trend: null when no turn in it wrote. */
export interface NavigationPoint {
  start: string
  reads: number | null
}

/**
 * Each bucket's mean reads before the first write, over the turns in it that wrote, as stats.get
 * counts a turn that navigated. The records carry the reads; the buckets' own means do not.
 */
export function navigationTrend(
  starts: string[],
  records: TurnMetrics[],
  by: StatsBucketSize,
): NavigationPoint[] {
  const wrote = records.filter((record) => (record.navigation?.write_calls ?? 0) > 0)
  return starts.map((start) => {
    const reads = wrote
      .filter((record) => bucketStart(record.closed_at, by) === start)
      .map((record) => record.navigation?.reads_before_first_write ?? 0)
    return {
      start,
      reads: reads.length === 0 ? null : reads.reduce((sum, count) => sum + count) / reads.length,
    }
  })
}

/** The UTC day a bucket starts on, as the chart and the drill-down name it. */
export function dayLabel(start: string): string {
  return new Date(`${start}T00:00:00Z`).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })
}

/** A bucket as a chart's tooltip names it: its UTC day, or the week that starts on it. */
export function bucketLabel(start: string, by: StatsBucketSize): string {
  return by === "week" ? `Week of ${dayLabel(start)}` : dayLabel(start)
}

/** Who started a turn. A turn whose start the log lacks counts as chat, as stats.get counts it. */
export function originLabel(origin: TurnMetrics["origin"]): string {
  return origin?.kind === "routine" ? `${origin.name} · ${origin.trigger}` : "Chat"
}

/** An origin row as the Origin tab names it: "Chat", or the routine's name. */
export function originName({ routine }: OriginUse): string {
  return routine ?? "Chat"
}
