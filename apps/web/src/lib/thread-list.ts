import { CallError } from "@kinby/contract"
import type {
  InstanceClient,
  ThreadFilter,
  ThreadListResult,
  ThreadStatus,
  ThreadSummary,
} from "@kinby/contract"

type Lister = Pick<InstanceClient, "call">

/**
 * An instance's threads in one set as it last listed them. The sidebar and the open thread read the
 * `sidebar` set. The open thread looks a thread outside it up in `all`, as the memory page does, so
 * an archived one still resolves.
 */
export interface ThreadList {
  view: () => ThreadListResult | undefined
  onChange: (listener: () => void) => () => void
  /**
   * List this set again. It stores after a `listKept` already in flight, and settles once stored.
   */
  list: () => Promise<void>
  /**
   * List the threads again in every set kept for the instance, so a change shows in the sidebar and
   * the open thread at once. They are stored together once all are read, so a thread an archive
   * takes out of the sidebar set never shows as missing from both.
   */
  listKept: () => Promise<void>
}

interface KeptList extends ThreadList {
  /** Read this set again, and return what stores it unless a newer read came since. */
  read: () => Promise<() => void>
}

// The lists live as long as the connection they read, like the thread stores beside them.
const lists = new WeakMap<Lister, Map<ThreadFilter, KeptList>>()
// The last refresh of every kept set. A newer read of one set stores after it, never between.
const refreshes = new WeakMap<Lister, Promise<void>>()

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

  const read = async () => {
    const mine = ++generation
    const next = await listThreads(client, filter)
    return () => {
      if (mine !== generation) return
      listed = next
      for (const listener of listeners) listener()
    }
  }
  return {
    view: () => listed,
    onChange(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    read,
    async list() {
      const store = await read()
      await refreshes.get(client)
      store()
    },
    async listKept() {
      const refresh = Promise.all([...kept.values()].map((list) => list.read())).then((stores) => {
        for (const store of stores) store()
      })
      refreshes.set(
        client,
        refresh.catch(() => {}),
      )
      await refresh
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

/** The statuses that wait on the user. */
const NEEDS_YOU: ReadonlySet<ThreadStatus> = new Set([
  "awaiting_approval",
  "awaiting_takeover",
  "awaiting_reply",
  "failed",
])

export function needsYou(thread: ThreadSummary): boolean {
  return NEEDS_YOU.has(thread.status)
}
