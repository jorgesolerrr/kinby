import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import { cn } from "cn"
import { useCallback, useLayoutEffect, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { InstanceAvatar } from "@/components/instance-avatar"
import { Onboarding } from "@/components/onboarding"
import { LibraryPage } from "@/components/library-page"
import { MainPanel } from "@/components/main-panel"
import { PageBoundary } from "@/components/page-boundary"
import { SignIn } from "@/components/sign-in"
import { SettingsDialog } from "@/components/settings-dialog"
import { Badge } from "@/components/ui/badge"
import { KinbyMark } from "@/components/kinby-mark"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { usePolled } from "@/hooks/use-polled"
import { instanceName } from "@/lib/instances"
import {
  replaceOldPath,
  useCreating,
  useInstanceTab,
  useLibraryOpen,
  usePath,
  useSelectedInstanceId,
  useSelectedThreadId,
  useSendTo,
  useSettingsOpen,
  useThreadsOpen,
  useViewOpen,
} from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"

export default function App({ client, clock = browserClock }: { client: Client; clock?: Clock }) {
  const state = useSyncExternalStore(client.onStateChange, client.state)

  if (state === "signed-out") return <SignIn onSignIn={client.signIn} />
  // A slow connection shows the mark, and a fast one shows nothing at all.
  if (state === "connecting") {
    return (
      <main className="grid min-h-svh place-items-center">
        <KinbyMark className="size-10 animate-in delay-300 fill-mode-backwards fade-in" />
      </main>
    )
  }
  return <Shell client={client} clock={clock} connected={state === "connected"} />
}

function Shell({ client, clock, connected }: { client: Client; clock: Clock; connected: boolean }) {
  const [listed, listAgain] = useInstances(client, clock, connected)
  const instances = listed?.instances
  const selectedId = useSelectedInstanceId()
  const creating = useCreating()
  const tab = useInstanceTab()
  const configOpen = tab === "config"
  const viewId = useViewOpen()
  const libraryOpen = useLibraryOpen()
  const settingsOpen = useSettingsOpen()
  const threadsOpen = useThreadsOpen()
  const sendTo = useSendTo()
  const path = usePath()
  // Before the page paints, so an old path never shows.
  useLayoutEffect(replaceOldPath, [path])
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
  // An open thread, a tab but Overview, or the library scrolls inside the window, not the page.
  const fixed = threadId !== undefined || (tab !== undefined && tab !== "overview") || libraryOpen

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar
          instances={instances ?? []}
          selected={selected}
          creating={creating}
          libraryOpen={libraryOpen}
          client={client}
          clock={clock}
          selectedClient={instanceClient}
          onSignOut={() => void client.signOut()}
        />
        <SidebarInset className={cn(fixed && "h-svh", "min-w-0")}>
          <header className="flex h-12 shrink-0 items-center gap-2 px-3">
            <SidebarTrigger />
            {selected !== undefined && threadId !== undefined && instanceClient !== undefined && (
              <OpenThread instance={selected} client={instanceClient} threadId={threadId} />
            )}
            {/* Mounted throughout, so a screen reader hears the connection drop. */}
            <output>{!connected && <Badge variant="destructive">Reconnecting</Badge>}</output>
          </header>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            <PageBoundary path={path}>
              {creating ? (
                <Onboarding caller={client} clock={clock} onPublished={listAgain} />
              ) : libraryOpen ? (
                <LibraryPage client={client} clock={clock} />
              ) : (
                <MainPanel
                  caller={client}
                  clock={clock}
                  instances={instances}
                  selected={selected}
                  threadId={threadId}
                  threadsOpen={threadsOpen}
                  tab={tab}
                  viewId={viewId}
                  to={sendTo}
                  instanceClient={instanceClient}
                  onChanged={listAgain}
                />
              )}
            </PageBoundary>
          </div>
        </SidebarInset>
        <SettingsDialog
          section={settingsOpen}
          client={client}
          clock={clock}
          removed={listed?.removed}
          onChanged={listAgain}
        />
      </SidebarProvider>
    </TooltipProvider>
  )
}

/**
 * The open thread's instance, by its avatar and name, and the thread's title once its instance
 * lists it, from the sidebar's threads or, for one outside them, from every thread.
 */
function OpenThread({
  instance,
  client,
  threadId,
}: {
  instance: InstanceSummary
  client: Pick<InstanceClient, "call">
  threadId: string
}) {
  const name = instanceName(instance)
  const sidebar = threadList(client, "sidebar")
  const all = threadList(client, "all")
  const inSidebar = useSyncExternalStore(sidebar.onChange, sidebar.view)
  const inAll = useSyncExternalStore(all.onChange, all.view)
  const thread = [...(inSidebar?.threads ?? []), ...(inAll?.threads ?? [])].find(
    (listed) => listed.id === threadId,
  )
  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <InstanceAvatar avatar={instance.avatar} name={name} size="sm" />
      <span className="shrink-0 text-muted-foreground">{name}</span>
      {thread !== undefined && (
        <>
          <span aria-hidden className="text-muted-foreground">
            /
          </span>
          <span className="truncate font-medium">{threadTitle(thread)}</span>
        </>
      )}
    </div>
  )
}

/** How often the instances are listed again while the page is visible. */
const LIST_INTERVAL_MS = 30_000

interface Listed {
  instances: InstanceSummary[]
  removed: InstanceSummary[]
}

/**
 * The hub's instances and its removed ones, listed together again as `usePolled` says. A caller
 * that opens an instance waits for the lists it asks for, so the page reads the lists that include
 * the change.
 */
function useInstances(
  client: Client,
  clock: Clock,
  connected: boolean,
): [Listed | undefined, () => Promise<void>] {
  const list = useCallback(
    () =>
      Promise.all([
        client.call("instance.list", {}),
        client.call("instance.list", { removed: true }),
      ]).then(([listed, removed]) => ({ instances: listed.instances, removed: removed.instances })),
    [client],
  )
  return usePolled(list, clock, connected, LIST_INTERVAL_MS)
}
