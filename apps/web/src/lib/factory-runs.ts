import type { FactoryRun, FactoryRunStatus } from "@kinby/contract"

import { when } from "@/lib/config-changes"

/** What each status of a run is called. */
export const STATUSES: Record<FactoryRunStatus, string> = {
  running: "Running",
  queued: "Queued",
  parked: "Parked",
  "needs-human": "Needs a human",
  done: "Done",
  cancelled: "Cancelled",
}

/** A run's work item as its values, "issue 7". */
export function workItem(run: FactoryRun): string {
  return Object.entries(run.work_item)
    .map(([name, value]) => `${name} ${String(value)}`)
    .join(", ")
}

/**
 * Where a run stands, "Running at review", or only its status once it has no step. A run parked
 * until its plan window resets says when it wakes.
 */
export function standing(run: FactoryRun): string {
  const status = run.step === null ? STATUSES[run.status] : `${STATUSES[run.status]} at ${run.step}`
  return run.wakes_at == null ? status : `${status} until ${when(run.wakes_at)}`
}

/** Whether a run has ended, done or cancelled. */
export function finished(run: FactoryRun): boolean {
  return run.status === "done" || run.status === "cancelled"
}

/**
 * The runs that need a human, then the others in flight, each in the order given, then the finished
 * ones, newest first.
 */
export function byAttention(runs: FactoryRun[]): FactoryRun[] {
  const needing = (run: FactoryRun) => run.status === "needs-human"
  return [
    ...runs.filter(needing),
    ...runs.filter((run) => !needing(run) && !finished(run)),
    ...runs.filter(finished).sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
  ]
}
