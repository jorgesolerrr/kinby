import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { CreateWizard } from "@/components/create-wizard"
import { MainPanel } from "@/components/main-panel"
import { SignIn } from "@/components/sign-in"
import { Badge } from "@/components/ui/badge"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useCreating, useSelectedInstanceId } from "@/lib/selection"

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
  // An instance the hub does not have, or no longer has, selects nothing.
  const selected = instances?.find((instance) => instance.instance_id === selectedId)

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar
          instances={instances ?? []}
          selected={selected}
          creating={creating}
          onSignOut={() => void client.signOut()}
        />
        <SidebarInset>
          <header className="flex h-12 items-center gap-2 px-2">
            <SidebarTrigger />
            {!connected && <Badge variant="destructive">Reconnecting</Badge>}
          </header>
          {creating ? (
            <CreateWizard caller={client} clock={clock} onPublished={listAgain} />
          ) : (
            <MainPanel
              caller={client}
              clock={clock}
              instances={instances}
              selected={selected}
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
 * The hub's instances, listed again each time the connection comes back, the window regains
 * focus, `LIST_INTERVAL_MS` passes while the page is visible, or when asked to. The list shown
 * stays until the next one arrives. The promise settles once that list is stored. A caller that
 * opens an instance waits for it, so the page reads the list that includes the change.
 */
function useInstances(
  client: Client,
  clock: Clock,
  connected: boolean,
): [InstanceSummary[] | undefined, () => Promise<void>] {
  const [instances, setInstances] = useState<InstanceSummary[]>()
  // A newer list, or a drop, retires the one already in flight.
  const generation = useRef(0)
  const connectedRef = useRef(false)
  const listAgain = useCallback(() => {
    if (!connectedRef.current) return Promise.resolve()
    const mine = ++generation.current
    return client.call("instance.list", {}).then(
      (listed) => {
        if (mine === generation.current) setInstances(listed.instances)
      },
      // A dropped socket shows as reconnecting, and the list stays as it was until it is back.
      () => {},
    )
  }, [client])
  useEffect(() => {
    connectedRef.current = connected
    if (!connected) {
      generation.current += 1
      return
    }
    void listAgain()
  }, [connected, listAgain])
  // Another tab or the CLI may change an instance, and a container may crash, with no word to this one.
  useEffect(() => {
    let cancel: (() => void) | undefined
    const poll = () => {
      cancel?.()
      cancel =
        document.visibilityState === "visible"
          ? clock.after(LIST_INTERVAL_MS, () => {
              void listAgain()
              poll()
            })
          : undefined
    }
    const onFocus = () => void listAgain()
    poll()
    document.addEventListener("visibilitychange", poll)
    window.addEventListener("focus", onFocus)
    return () => {
      cancel?.()
      document.removeEventListener("visibilitychange", poll)
      window.removeEventListener("focus", onFocus)
    }
  }, [clock, listAgain])
  return [instances, listAgain]
}
