import type { Client, InstanceSummary, PickState } from "@kinby/contract"

import { lost } from "@/lib/operation"

/** An instance's pick of a library entry, and how its copy stands. */
export interface InstancePick {
  instance: InstanceSummary
  state: PickState
}

/** Every instance's picks of the skills and the MCP servers, by the name of the entry picked. */
export type PicksByEntry = Record<"skill" | "mcp_server", Map<string, InstancePick[]>>

/**
 * Read each instance's picks. The hub reads them from the instance's files, so a stopped instance
 * answers too. An instance whose picks fail to read is left out. A dropped connection fails the
 * whole read, so a retry reads every instance again.
 */
export async function readPicks(client: Pick<Client, "call">): Promise<PicksByEntry> {
  const { instances } = await client.call("instance.list", {})
  const read = await Promise.all(
    instances.map((instance) =>
      client.call("instance.picks.get", { instance_id: instance.instance_id }).then(
        (picks) => ({ instance, picks }),
        (error: unknown) => {
          if (lost(error)) throw error
          return undefined
        },
      ),
    ),
  )
  const byEntry: PicksByEntry = { skill: new Map(), mcp_server: new Map() }
  const add = (kind: keyof PicksByEntry, name: string, pick: InstancePick) =>
    byEntry[kind].set(name, [...(byEntry[kind].get(name) ?? []), pick])
  for (const { instance, picks } of read.filter((each) => each !== undefined)) {
    for (const { name, state } of picks.picks) add("skill", name, { instance, state })
    for (const { name, state } of picks.mcp_servers) add("mcp_server", name, { instance, state })
  }
  return byEntry
}
