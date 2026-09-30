import type {
  ConfigChange,
  InstanceClient,
  RoutineFile,
  RoutineRunOutcome,
  RoutineSummary,
} from "@kinby/contract"

import { latestChange, unlessStale, when } from "@/lib/config-changes"

type Caller = Pick<InstanceClient, "call">

/** A routine as the list shows it, and the latest change to its directory. */
export interface ListedRoutine {
  summary: RoutineSummary
  lastChange: ConfigChange | undefined
}

/** The directory a routine lives in, as the config changes name it. */
function routineFile(name: string): string {
  return `routines/${name}`
}

export async function listRoutines(caller: Caller): Promise<ListedRoutine[]> {
  const { routines } = await caller.call("routine.list", {})
  return Promise.all(
    routines.map(async (summary) => ({
      summary,
      lastChange: await latestChange(caller, routineFile(summary.name)),
    })),
  )
}

/**
 * Write `content` as the routine's ROUTINE.md over the routine read with `hash`, or create it
 * when `hash` is null. "stale" when the routine changed since, or already exists.
 */
export function saveRoutine(
  caller: Caller,
  name: string,
  content: string,
  hash: string | null,
): Promise<RoutineFile | "stale"> {
  return unlessStale(caller.call("routine.write", { name, content, hash }))
}

/** Delete the routine read with `hash`. "stale" when it changed since. */
export async function deleteRoutine(
  caller: Caller,
  name: string,
  hash: string,
): Promise<"deleted" | "stale"> {
  const deleted = await unlessStale(caller.call("routine.delete", { name, hash }))
  return deleted === "stale" ? "stale" : "deleted"
}

/** What starts the routine besides a manual run. */
export function trigger({ schedule, signal }: RoutineSummary): string {
  if (schedule !== null) return `Schedule ${schedule}`
  if (signal) return `Signal at ${signal.path}`
  return "Runs only when started by hand"
}

export function nextFiring({ next_run }: RoutineSummary): string {
  return next_run === null ? "No next firing" : `Next firing ${when(next_run)}`
}

const OUTCOMES: Record<RoutineRunOutcome, string> = {
  running: "running",
  parked: "waiting on an approval",
  work: "did work",
  "no-work": "nothing new",
  failed: "failed",
  interrupted: "interrupted",
}

export function lastFiring({ last_run }: RoutineSummary): string {
  if (last_run === null) return "Never fired"
  return `Last firing ${when(last_run.started_at)}, ${OUTCOMES[last_run.outcome]}`
}

/** The failures a routine had since it last fired without one. */
export function failuresInARow(failures: number): string {
  return `${failures} ${failures === 1 ? "failure" : "failures"} in a row`
}

/** The template a new routine starts from: the frontmatter keys a routine needs first. */
export const NEW_ROUTINE = "---\ndescription: \nschedule: 0 9 * * *\n---\n"
