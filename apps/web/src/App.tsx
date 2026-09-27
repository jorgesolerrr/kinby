import type { Client, InstanceSummary } from "@kinby/contract"
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { CreateWizard } from "@/components/create-wizard"
import { MainPanel } from "@/components/main-panel"
import { SignIn } from "@/components/sign-in"
import { Badge } from "@/components/ui/badge"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useCreating, useSelectedInstanceId } from "@/lib/selection"

export default function App({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.onStateChange, client.state)

  if (state === "signed-out") return <SignIn onSignIn={client.signIn} />
  if (state === "connecting") return null
  return <Shell client={client} connected={state === "connected"} />
}

function Shell({ client, connected }: { client: Client; connected: boolean }) {
  const [instances, listAgain] = useInstances(client, connected)
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
            <CreateWizard caller={client} onPublished={listAgain} />
          ) : (
            <MainPanel
              caller={client}
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

/**
 * The hub's instances, listed again each time the connection comes back, or when asked to.
 * The promise settles once that list is stored. A caller that opens an instance waits for it,
 * so the page reads the list that includes the change.
 */
function useInstances(
  client: Client,
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
  return [instances, listAgain]
}
