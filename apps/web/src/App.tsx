import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { cn } from "cn"
import { MessagesSquareIcon } from "lucide-react"
import { useCallback, useLayoutEffect, useState, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { Onboarding } from "@/components/onboarding"
import { LibraryPage } from "@/components/library-page"
import { MainPanel } from "@/components/main-panel"
import { PageBoundary } from "@/components/page-boundary"
import { SignIn } from "@/components/sign-in"
import { SettingsDialog } from "@/components/settings-dialog"
import { ThreadsColumn } from "@/components/threads-column"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { KinbyMark } from "@/components/kinby-mark"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { useIsMobile } from "@/hooks/use-mobile"
import { usePolled } from "@/hooks/use-polled"
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
  const isMobile = useIsMobile()
  const [threadsShown, setThreadsShown] = useState(false)
  // The threads column stands beside the sidebar for the instance the hub relays to. A hub page
  // selects no instance, so it shows none.
  const columnFor = instanceClient !== undefined && running !== undefined ? selected : undefined
  const column = columnFor !== undefined && instanceClient !== undefined && (
    <ThreadsColumn
      key={columnFor.instance_id}
      client={instanceClient}
      clock={clock}
      instance={columnFor}
      onPicked={() => setThreadsShown(false)}
    />
  )
  // An open thread, a tab but Overview, the library, or a page beside the threads column scrolls
  // inside the window, not the page.
  const fixed =
    threadId !== undefined ||
    (tab !== undefined && tab !== "overview") ||
    libraryOpen ||
    (column !== false && !isMobile)

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
          selectedClient={columnFor !== undefined ? instanceClient : undefined}
          onSignOut={() => void client.signOut()}
        />
        <SidebarInset className={cn(fixed && "h-svh", "min-w-0 md:flex-row")}>
          {column !== false && !isMobile && (
            <div className="flex w-80 shrink-0 flex-col border-r">{column}</div>
          )}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <header className="flex h-12 shrink-0 items-center gap-2 px-3">
              <SidebarTrigger />
              {column !== false && isMobile && (
                <Sheet open={threadsShown} onOpenChange={setThreadsShown}>
                  <SheetTrigger
                    render={<Button variant="ghost" size="icon-sm" aria-label="Threads" />}
                  >
                    <MessagesSquareIcon />
                  </SheetTrigger>
                  <SheetContent side="left" showCloseButton={false}>
                    <SheetTitle className="sr-only">Threads</SheetTitle>
                    {column}
                  </SheetContent>
                </Sheet>
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
