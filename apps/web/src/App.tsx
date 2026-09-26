import type { Client, InstanceSummary } from "@kinby/contract"
import { useCallback, useEffect, useState, useSyncExternalStore } from "react"

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
            <MainPanel instances={instances} selected={selected} />
          )}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}

/** The hub's instances, listed again each time the connection comes back, or when asked to. */
function useInstances(
  client: Client,
  connected: boolean,
): [InstanceSummary[] | undefined, () => void] {
  const [instances, setInstances] = useState<InstanceSummary[]>()
  const [listing, setListing] = useState(0)
  const listAgain = useCallback(() => setListing((count) => count + 1), [])
  useEffect(() => {
    if (!connected) return
    let current = true
    client.call("instance.list", {}).then(
      (listed) => {
        if (current) setInstances(listed.instances)
      },
      // A dropped socket shows as reconnecting, and the list stays as it was until it is back.
      () => {},
    )
    return () => {
      current = false
    }
  }, [client, connected, listing])
  return [instances, listAgain]
}
