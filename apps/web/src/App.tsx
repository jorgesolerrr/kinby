import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import { cn } from "cn"
import { useCallback, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { CreateWizard } from "@/components/create-wizard"
import { MainPanel } from "@/components/main-panel"
import { NavThreads } from "@/components/nav-threads"
import { SignIn } from "@/components/sign-in"
import { UsagePage } from "@/components/usage-page"
import { Badge } from "@/components/ui/badge"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useFollowing } from "@/hooks/use-following"
import { usePolled } from "@/hooks/use-polled"
import {
  useConfigOpen,
  useCreating,
  useMemoryOpen,
  useSelectedInstanceId,
  useSelectedThreadId,
  useStatsOpen,
  useUsageOpen,
} from "@/lib/selection"

export default function App({ client, clock = browserClock }: { client: Client; clock?: Clock }) {
  const state = useSyncExternalStore(client.onStateChange, client.state)

  if (state === "signed-out") return <SignIn onSignIn={client.signIn} />
  if (state === "connecting") return null
  return <Shell client={client} clock={clock} connected={state === "connected"} />
}

function Shell({ client, clock, connected }: { client: Client; clock: Clock; connected: boolean }) {
  const [instances, listAgain] = useInstances(client, clock, connected)
  const selectedId = useSelectedInstanceId()
  const creating = useCreating()
  const configOpen = useConfigOpen()
  const memoryOpen = useMemoryOpen()
  const statsOpen = useStatsOpen()
  const usageOpen = useUsageOpen()
  // An instance the hub does not have, or no longer has, selects nothing.
  const selected = instances?.find((instance) => instance.instance_id === selectedId)
  // The hub relays only to a running instance. An open config panel keeps its connection while the
  // instance is meant to run, so an update that restarts the container does not close the panel.
  const running =
    selected?.process === "running" || (configOpen && selected?.intended_state === "running")
      ? selected.instance_id
      : undefined
  const instanceClient = useInstanceClient(client, running)
  const threadId = useSelectedThreadId()

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar
          instances={instances ?? []}
          selected={selected}
          creating={creating}
          usageOpen={usageOpen}
          threads={
            instanceClient !== undefined &&
            running !== undefined && (
              <NavThreads client={instanceClient} clock={clock} instanceId={running} />
            )
          }
          onSignOut={() => void client.signOut()}
        />
        {/* An open thread, config panel, memory page, or stats page scrolls inside the window, not
            the page. */}
        <SidebarInset
          className={cn(
            (threadId !== undefined || configOpen || memoryOpen || statsOpen) && "h-svh",
          )}
        >
          <header className="flex h-12 items-center gap-2 px-2">
            <SidebarTrigger />
            {!connected && <Badge variant="destructive">Reconnecting</Badge>}
          </header>
          {creating ? (
            <CreateWizard caller={client} clock={clock} onPublished={listAgain} />
          ) : usageOpen ? (
            <UsagePage client={client} clock={clock} />
          ) : (
            <MainPanel
              caller={client}
              clock={clock}
              instances={instances}
              selected={selected}
              threadId={threadId}
              configOpen={configOpen}
              memoryOpen={memoryOpen}
              statsOpen={statsOpen}
              instanceClient={instanceClient}
              onChanged={listAgain}
            />
          )}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}

/** How often the instances are listed again while the page is visible. */
const LIST_INTERVAL_MS = 30_000

/**
 * The hub's instances, listed again as `usePolled` says. A caller that opens an instance waits for
 * the list it asks for, so the page reads the list that includes the change.
 */
function useInstances(
  client: Client,
  clock: Clock,
  connected: boolean,
): [InstanceSummary[] | undefined, () => Promise<void>] {
  const list = useCallback(
    () => client.call("instance.list", {}).then((listed) => listed.instances),
    [client],
  )
  return usePolled(list, clock, connected, LIST_INTERVAL_MS)
}

/** One instance's own connection, open while `instanceId` names it and closed after. */
function useInstanceClient(
  client: Client,
  instanceId: string | undefined,
): InstanceClient | undefined {
  const open = useCallback(
    (instanceId: string, report: (opened: InstanceClient) => void) => {
      const opened = client.instance(instanceId)
      report(opened)
      return () => opened.close()
    },
    [client],
  )
  return useFollowing(instanceId, open)
}
