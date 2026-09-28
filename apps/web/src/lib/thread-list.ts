import type { InstanceClient, ThreadListResult, ThreadSummary } from "@kinby/contract"

type Lister = Pick<InstanceClient, "call">

/** An instance's threads as it last listed them, which the sidebar and the open thread share. */
export interface ThreadList {
  view: () => ThreadListResult | undefined
  onChange: (listener: () => void) => () => void
  /** List the threads again. The promise settles once the list is stored. */
  list: () => Promise<void>
}

// A list lives as long as the connection it reads, like the thread stores beside it.
const lists = new WeakMap<Lister, ThreadList>()

/** The thread list of `client`'s instance. */
export function threadList(client: Lister): ThreadList {
  let list = lists.get(client)
  if (list === undefined) {
    list = createThreadList(client)
    lists.set(client, list)
  }
  return list
}

function createThreadList(client: Lister): ThreadList {
  let listed: ThreadListResult | undefined
  const listeners = new Set<() => void>()
  // A newer read retires the one already in flight.
  let generation = 0

  return {
    view: () => listed,
    onChange(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    async list() {
      const mine = ++generation
      const next = await client.call("thread.list", {})
      if (mine !== generation) return
      listed = next
      for (const listener of listeners) listener()
    },
  }
}

export function threadTitle(thread: ThreadSummary): string {
  return thread.title ?? "Untitled thread"
}
