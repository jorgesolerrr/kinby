import type { InstanceClient } from "@kinby/contract"
import { useSyncExternalStore } from "react"

import { threadList } from "@/lib/thread-list"

/**
 * Thread `threadId` as its instance last listed it, the ceiling on its mode, and the list it came
 * from: the sidebar's, or the instance's full list for a thread outside it, so an archived thread
 * still resolves.
 */
export function useListedThread(client: Pick<InstanceClient, "call">, threadId: string) {
  const sidebar = threadList(client, "sidebar")
  const all = threadList(client, "all")
  const inSidebar = useSyncExternalStore(sidebar.onChange, sidebar.view)
  const inAll = useSyncExternalStore(all.onChange, all.view)
  const outside =
    inSidebar !== undefined && !inSidebar.threads.some((summary) => summary.id === threadId)
  const [threads, listed] = outside ? [all, inAll] : [sidebar, inSidebar]
  return {
    threads,
    thread: listed?.threads.find((summary) => summary.id === threadId),
    ceiling: listed?.ceiling,
  }
}
