import type { ConfigActor, ConfigChange, InstanceClient } from "@kinby/contract"

type Caller = Pick<InstanceClient, "call">

const ACTORS: Record<ConfigActor, string> = {
  app: "you",
  agent: "the agent",
  failure_policy: "the routine failure policy",
}

const WHEN = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" })

/** Who changed a file last and when, or that nobody has since the log began. */
export function lastChanged(change: ConfigChange | undefined): string {
  if (change === undefined) return "Never changed"
  return `Last changed by ${ACTORS[change.actor]}, ${WHEN.format(new Date(change.at))}`
}

/** The latest change to `file`, if the log has one. */
export async function latestChange(
  caller: Caller,
  file: string,
): Promise<ConfigChange | undefined> {
  const history = await caller.call("config.history", { file, limit: 1 })
  return history.changes[0]
}
