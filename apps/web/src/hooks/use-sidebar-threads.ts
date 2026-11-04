import type { Clock, InstanceClient, ThreadSummary } from "@kinby/contract"
import { useSyncExternalStore } from "react"

import { POLL_INTERVAL_MS, usePolled } from "@/hooks/use-polled"
import { threadList } from "@/lib/thread-list"

/**
 * The sidebar set of `client`'s instance, listed again as `usePolled` says, or undefined until it is
 * first listed. It is the list the open thread reads and changes, so a change there shows here at
 * once. Undefined while nothing reaches the instance.
 */
export function useSidebarThreads(
  client: Pick<InstanceClient, "call" | "state" | "onStateChange"> | undefined,
  clock: Clock,
): ThreadSummary[] | undefined {
  const list = client === undefined ? undefined : threadList(client, "sidebar")
  const connected =
    useSyncExternalStore(client?.onStateChange ?? unsubscribed, () => client?.state()) ===
    "connected"
  usePolled(list?.list ?? listNothing, clock, connected, POLL_INTERVAL_MS)
  return useSyncExternalStore(list?.onChange ?? unsubscribed, () => list?.view())?.threads
}

function unsubscribed(): () => void {
  return () => {}
}

async function listNothing(): Promise<void> {}
