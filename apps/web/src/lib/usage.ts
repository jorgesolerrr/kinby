import type { InstanceSummary, RunTotals, StatsBucket, StatsSummaryResult } from "@kinby/contract"

import { turnCount } from "@/lib/stats"

/** An instance the hub summed, with its buckets and its factory runs. */
export interface CountedRow {
  instance: InstanceSummary
  buckets: StatsBucket[]
  runs: RunTotals
}

/** An instance the hub left out of the totals, and why. */
interface UncountedRow {
  instance: InstanceSummary
  notCounted: string
}

/** An instance as the Usage page lists it. */
export type UsageRow = CountedRow | UncountedRow

/** The instances `usage` names, in the order the hub lists them. */
export function usageRows(usage: StatsSummaryResult, instances: InstanceSummary[]): UsageRow[] {
  return instances.flatMap((instance): UsageRow[] => {
    const id = instance.instance_id
    const buckets = usage.buckets[id]
    if (buckets !== undefined) return [{ instance, buckets, runs: usage.runs[id] }]
    if (usage.skipped.includes(id)) return [{ instance, notCounted: "not running" }]
    if (usage.unreachable.includes(id)) return [{ instance, notCounted: "didn't answer" }]
    if (usage.outdated.includes(id)) {
      return [{ instance, notCounted: "runs an older kinby, update core" }]
    }
    // Made after the hub summed, so there is nothing to show for it yet.
    return []
  })
}

/** One bucket on a chart across instances, with each instance's value in it. */
export interface InstancePoint {
  start: string
  values: (number | null)[]
}

/**
 * A point per start, holding `value` for each instance in the order given. An instance with
 * nothing in a bucket has 0 there.
 */
export function instancePoints(
  starts: string[],
  instances: StatsBucket[][],
  value: (bucket: StatsBucket) => number | null,
): InstancePoint[] {
  return starts.map((start) => ({
    start,
    values: instances.map((buckets) => {
      const bucket = buckets.find((each) => each.start === start)
      return bucket === undefined ? 0 : value(bucket)
    }),
  }))
}

/** Every turn that closed in `buckets`. */
export function turnsIn(buckets: StatsBucket[]): number {
  return buckets.reduce((turns, bucket) => turns + turnCount(bucket), 0)
}

/** The API cost of the priced buckets, or null when none was priced, so it never reads as 0. */
export function costIn(buckets: StatsBucket[]): number | null {
  const priced = buckets.flatMap((bucket) => (bucket.cost == null ? [] : [bucket.cost]))
  return priced.length === 0 ? null : priced.reduce((cost, next) => cost + next)
}

/** Every plan's runs in `bucket`. */
export function planRuns(bucket: StatsBucket): number {
  return bucket.subscriptions.reduce((runs, use) => runs + (use.runs ?? 0), 0)
}

/** Every plan's runs in `buckets`. */
export function planRunsIn(buckets: StatsBucket[]): number {
  return buckets.reduce((runs, bucket) => runs + planRuns(bucket), 0)
}
