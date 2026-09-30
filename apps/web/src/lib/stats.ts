import type {
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
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return { since: new Date(today - (range - 1) * DAY_MS).toISOString(), by: bucketSize(range) }
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

/** An API cost in dollars, or a dash when no turn in it was priced. */
export function money(cost: number | null | undefined): string {
  return cost === null || cost === undefined ? "—" : `$${cost.toFixed(2)}`
}

/** The bucket a turn closed in, named as stats.get names it: its UTC day, or the Monday of that week. */
function bucketStart(closedAt: string, by: StatsBucketSize): string {
  const closed = new Date(closedAt)
  const day = Date.UTC(closed.getUTCFullYear(), closed.getUTCMonth(), closed.getUTCDate())
  const monday = by === "week" ? ((closed.getUTCDay() + 6) % 7) * DAY_MS : 0
  return new Date(day - monday).toISOString().slice(0, 10)
}

/** The turns that closed in the bucket starting on `start`, most expensive first and unpriced last. */
export function bucketTurns(
  records: TurnMetrics[],
  start: string,
  by: StatsBucketSize,
): TurnMetrics[] {
  return records
    .filter((record) => bucketStart(record.closed_at, by) === start)
    .sort((a, b) => (b.cost ?? -1) - (a.cost ?? -1))
}

/** The UTC day a bucket starts on, as the chart and the drill-down name it. */
export function dayLabel(start: string): string {
  return new Date(`${start}T00:00:00Z`).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })
}

/** Who started a turn. A turn whose start the log lacks counts as chat, as stats.get counts it. */
export function originLabel(origin: TurnMetrics["origin"]): string {
  return origin?.kind === "routine" ? `${origin.name} · ${origin.trigger}` : "Chat"
}
