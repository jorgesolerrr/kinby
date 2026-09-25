import type { Client, InstanceSummary } from "@kinby/contract"
import { useEffect, useState, useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { MainPanel } from "@/components/main-panel"
import { SignIn } from "@/components/sign-in"
import { Badge } from "@/components/ui/badge"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useSelectedInstanceId } from "@/lib/selection"

export default function App({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.onStateChange, client.state)

  if (state === "signed-out") return <SignIn onSignIn={client.signIn} />
  if (state === "connecting") return null
  return <Shell client={client} disconnected={state === "disconnected"} />
}

function Shell({ client, disconnected }: { client: Client; disconnected: boolean }) {
  const instances = useInstances(client)
  const selectedId = useSelectedInstanceId()
  // An instance the hub does not have, or no longer has, selects nothing.
  const selected = instances?.find((instance) => instance.instance_id === selectedId)

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar
          instances={instances ?? []}
          selected={selected}
          onSignOut={() => void client.signOut()}
        />
        <SidebarInset>
          <header className="flex h-12 items-center gap-2 px-2">
            <SidebarTrigger />
            {disconnected && <Badge variant="destructive">Disconnected</Badge>}
          </header>
          <MainPanel instances={instances} selected={selected} />
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}

/** The hub's instances, once `instance.list` answers. */
function useInstances(client: Client): InstanceSummary[] | undefined {
  const [instances, setInstances] = useState<InstanceSummary[]>()
  useEffect(() => {
    let current = true
    client.call("instance.list", {}).then(
      (listed) => {
        if (current) setInstances(listed.instances)
      },
      // A dropped socket already shows as disconnected, and the list stays as it was.
      () => {},
    )
    return () => {
      current = false
    }
  }, [client])
  return instances
}
