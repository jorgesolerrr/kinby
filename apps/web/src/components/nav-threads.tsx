import type { Clock, InstanceClient, ThreadStatus } from "@kinby/contract"
import { type ReactNode, useSyncExternalStore } from "react"

import { Badge } from "@/components/ui/badge"
import {
  SidebarMenuAction,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar"
import { usePolled } from "@/hooks/use-polled"
import { selectThread, threadPath, useSelectedThreadId } from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"
import { PlusIcon } from "lucide-react"

/** How often the threads are listed again while the page is visible. */
const LIST_INTERVAL_MS = 5_000

/** The badge each status shows. An idle thread needs no look, so it shows none. */
const STATUS_BADGES: Record<ThreadStatus, ReactNode> = {
  idle: null,
  running: <Badge variant="secondary">working</Badge>,
  awaiting_approval: <Badge>needs you</Badge>,
  failed: <Badge variant="destructive">failed</Badge>,
}

/** The plus that starts a thread on the instance's row, and the instance's threads under it. */
export function NavThreads({
  client,
  clock,
  instanceId,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  clock: Clock
  instanceId: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const list = threadList(client)
  const [, listAgain] = usePolled(list.list, clock, connected, LIST_INTERVAL_MS)
  const threads = useSyncExternalStore(list.onChange, list.view)?.threads
  const selectedId = useSelectedThreadId()

  const startThread = () =>
    client.call("thread.create", {}).then(
      (created) => {
        selectThread(instanceId, created.id)
        return listAgain()
      },
      // A dropped socket shows as reconnecting. Nothing was started, so there is nothing to show.
      () => {},
    )

  return (
    <>
      <SidebarMenuAction disabled={!connected} onClick={() => void startThread()}>
        <PlusIcon />
        <span className="sr-only">New thread</span>
      </SidebarMenuAction>
      {threads !== undefined && threads.length > 0 && (
        <SidebarMenuSub>
          {threads.map((thread) => {
            const isSelected = thread.id === selectedId
            return (
              <SidebarMenuSubItem key={thread.id}>
                <SidebarMenuSubButton
                  isActive={isSelected}
                  aria-current={isSelected ? "page" : undefined}
                  href={threadPath(instanceId, thread.id)}
                  onClick={(event) => {
                    event.preventDefault()
                    selectThread(instanceId, thread.id)
                  }}
                >
                  <span className="min-w-0 truncate">{threadTitle(thread)}</span>
                  {STATUS_BADGES[thread.status]}
                </SidebarMenuSubButton>
              </SidebarMenuSubItem>
            )
          })}
        </SidebarMenuSub>
      )}
    </>
  )
}
