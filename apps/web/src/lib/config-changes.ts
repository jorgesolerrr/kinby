import { CallError } from "@kinby/contract"
import type {
  ConfigActor,
  ConfigChange,
  FactoryVersion,
  FactoryVersionSource,
  InstanceClient,
} from "@kinby/contract"

type Caller = Pick<InstanceClient, "call">

const ACTORS: Record<ConfigActor, string> = {
  app: "you",
  agent: "the agent",
  failure_policy: "the routine failure policy",
}

const WHEN = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" })

/** A time the instance sent, in the reader's clock. */
export function when(at: string): string {
  return WHEN.format(new Date(at))
}

/**
 * Who changed a file last and when, or that nobody has since the log began. `hash` is the one
 * the file's read returned: when it differs from what the change left, the file changed outside
 * kinby since, and the logged actor no longer gets the credit.
 */
export function lastChanged(change: ConfigChange | undefined, hash: string): string {
  if (change === undefined) return "Never changed"
  if (change.hash != null && change.hash !== hash) return "Changed outside kinby"
  return `Last changed by ${ACTORS[change.actor]}, ${when(change.at)}`
}

/** Where each factory version came from, as History words it. */
export const VERSION_SOURCES: Record<FactoryVersionSource, string> = {
  web: "Saved from the web",
  chat: "Changed from chat",
  by_hand: "Changed by hand",
  revert: "Reverted",
  shipped: "kinby's version",
  shipped_update: "kinby's update, merged",
}

/** The newest of a factory's `versions`, newest first, that changed `step`, if one did. */
export function stepLastChange(
  versions: FactoryVersion[],
  step: string,
): FactoryVersion | undefined {
  return versions.find((version) => version.steps.includes(step))
}

/**
 * Where a step's last change came from and when. The first version of a shipped factory lists
 * every step, so a step only it lists is still kinby's, not a change the user made.
 */
export function stepChanged(version: FactoryVersion): string {
  if (version.source === "shipped") {
    return `${VERSION_SOURCES.shipped}, unchanged since ${when(version.time)}`
  }
  return `${VERSION_SOURCES[version.source]} on ${when(version.time)}`
}

/** The latest change to `file`, if the log has one. */
export async function latestChange(
  caller: Caller,
  file: string,
): Promise<ConfigChange | undefined> {
  const history = await caller.call("config.history", { file, limit: 1 })
  return history.changes[0]
}

/**
 * A read of `file` and the latest change to it, as one view of the file. They are two calls, so a
 * save landing between them can pair a read from one side of it with a change from the other.
 * When their hashes differ, both run once more: a save that finished meanwhile then shows in
 * both, and only a change made outside kinby keeps them apart.
 */
export async function readWithChange<R extends { hash: string }>(
  caller: Caller,
  file: string,
  read: () => Promise<R>,
): Promise<[R, ConfigChange | undefined]> {
  const first = await Promise.all([read(), latestChange(caller, file)])
  const [result, change] = first
  if (change?.hash == null || change.hash === result.hash) return first
  return Promise.all([read(), latestChange(caller, file)])
}

/** A write's result, or "stale" when the instance refused it over a change made since the read. */
export async function unlessStale<T>(write: Promise<T>): Promise<T | "stale"> {
  try {
    return await write
  } catch (error) {
    if (error instanceof CallError && error.code === "STALE") return "stale"
    throw error
  }
}
