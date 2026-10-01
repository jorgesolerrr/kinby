import type {
  OriginUse,
  StatsBucket,
  StatsBucketSize,
  StatsGetCommand,
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

/** A token count with thousands separators. */
export function tokens(count: number): string {
  return count.toLocaleString("en")
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
