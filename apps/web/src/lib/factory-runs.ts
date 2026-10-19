import type { FactoryRun, FactoryRunStatus } from "@kinby/contract"

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

/** Where a run stands, "Running at review", or only its status once it has no step. */
export function standing(run: FactoryRun): string {
  return run.step === null ? STATUSES[run.status] : `${STATUSES[run.status]} at ${run.step}`
}
