import { CallError } from "@kinby/contract"
import type { InstanceClient, ThreadFilter, ThreadListResult, ThreadSummary } from "@kinby/contract"

type Lister = Pick<InstanceClient, "call">

/**
 * An instance's threads in one set as it last listed them. The sidebar reads the `sidebar` set, and
 * the open thread and the memory page look a thread up in `all`, so an archived one still resolves.
 */
export interface ThreadList {
  view: () => ThreadListResult | undefined
  onChange: (listener: () => void) => () => void
  /**
   * List the threads again in every set kept for the instance, so a change shows in the sidebar and
   * the open thread at once. The promise settles once the lists are stored.
   */
  list: () => Promise<void>
}

interface KeptList extends ThreadList {
  /** List this set alone again. */
  listOwn: () => Promise<void>
}

// The lists live as long as the connection they read, like the thread stores beside them.
const lists = new WeakMap<Lister, Map<ThreadFilter, KeptList>>()

/** The `filter` set of `client`'s instance's threads. */
export function threadList(client: Lister, filter: ThreadFilter): ThreadList {
  let kept = lists.get(client)
  if (kept === undefined) {
    kept = new Map()
    lists.set(client, kept)
  }
  let list = kept.get(filter)
  if (list === undefined) {
    list = createThreadList(client, filter, kept)
    kept.set(filter, list)
  }
  return list
}

function createThreadList(
  client: Lister,
  filter: ThreadFilter,
  kept: Map<ThreadFilter, KeptList>,
): KeptList {
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
      await Promise.all([...kept.values()].map((list) => list.listOwn()))
    },
    async listOwn() {
      const mine = ++generation
      const next = await listThreads(client, filter)
      if (mine !== generation) return
      listed = next
      for (const listener of listeners) listener()
    },
  }
}

/**
 * The `filter` set. A core from before archiving refuses the filter. It has no archived thread, so
 * its whole list is the sidebar set and the all set, and its archived set is empty.
 */
async function listThreads(client: Lister, filter: ThreadFilter): Promise<ThreadListResult> {
  try {
    return await client.call("thread.list", { filter })
  } catch (error) {
    if (!(error instanceof CallError && error.code === "INVALID_ARGUMENT")) throw error
    const listed = await client.call("thread.list", {})
    return filter === "archived" ? { ...listed, threads: [] } : listed
  }
}

export function threadTitle(thread: ThreadSummary): string {
  return thread.title ?? "Untitled thread"
}
