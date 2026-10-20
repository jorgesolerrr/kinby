import type { FactoryRun, FactoryRunStatus } from "@kinby/contract"

import { when } from "@/lib/config-changes"

const STATUSES: Record<FactoryRunStatus, string> = {
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

/** The runs that need a human, then the others, each in the order given. */
export function needingHumanFirst(runs: FactoryRun[]): FactoryRun[] {
  const needing = (run: FactoryRun) => run.status === "needs-human"
  return [...runs.filter(needing), ...runs.filter((run) => !needing(run))]
}
