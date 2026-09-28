import type { InstanceClient } from "@kinby/contract"

import { reason } from "@/lib/operation"
import { EMPTY_TIMELINE, project, type Timeline } from "@/lib/timeline"

type Follower = Pick<InstanceClient, "subscribe">

/** What the panel shows of a thread. */
interface ThreadView {
  timeline: Timeline
  /** Whether the replay reached the head the hub subscribed at, so the timeline is the whole history. */
  replayed: boolean
  /** Why the thread could not be followed. */
  failure?: string
}

/** One thread's timeline, its listeners, and the subscription that feeds it. */
export interface ThreadStore {
  view: () => ThreadView
  onChange: (listener: () => void) => () => void
  /** Follow the thread's events after the last one projected. The returned function stops it. */
  follow: () => () => void
}

// A store lives as long as the connection it follows, so reopening a thread resumes where it was.
const stores = new WeakMap<Follower, Map<string, ThreadStore>>()

/** The store of `threadId` on `client`'s connection. */
export function threadStore(client: Follower, threadId: string): ThreadStore {
  let threads = stores.get(client)
  if (threads === undefined) {
    threads = new Map()
    stores.set(client, threads)
  }
  let store = threads.get(threadId)
  if (store === undefined) {
    store = createThreadStore(client, threadId)
    threads.set(threadId, store)
  }
  return store
}

function createThreadStore(client: Follower, threadId: string): ThreadStore {
  let view: ThreadView = { timeline: EMPTY_TIMELINE, replayed: false }
  const listeners = new Set<() => void>()
  const show = (next: ThreadView) => {
    view = next
    for (const listener of listeners) listener()
  }

  return {
    view: () => view,
    onChange(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    follow() {
      const subscription = client.subscribe("thread.subscribe", {
        thread_id: threadId,
        after_sequence: view.timeline.sequence,
      })
      let following = true
      let head: number | undefined
      const reached = (timeline: Timeline) => head !== undefined && timeline.sequence >= head
      show({ timeline: view.timeline, replayed: false })

      void subscription.head.then((sequence) => {
        head = sequence
        if (following) show({ ...view, replayed: reached(view.timeline) })
      })
      const read = async () => {
        try {
          for await (const event of subscription) {
            if (!following) return
            const timeline = project(view.timeline, event)
            show({ ...view, timeline, replayed: reached(timeline) })
          }
        } catch (error) {
          if (following) show({ ...view, failure: reason(error) })
        }
      }
      void read()

      return () => {
        following = false
        subscription.cancel()
      }
    },
  }
}
