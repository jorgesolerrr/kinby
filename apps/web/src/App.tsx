import type { Client } from "@kinby/contract"
import { useSyncExternalStore } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { SignIn } from "@/components/sign-in"
import { Badge } from "@/components/ui/badge"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"

export default function App({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.onStateChange, client.state)

  if (state === "signed-out") return <SignIn onSignIn={client.signIn} />
  if (state === "connecting") return null
  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar onSignOut={() => void client.signOut()} />
        <SidebarInset>
          <header className="flex h-12 items-center gap-2 px-2">
            <SidebarTrigger />
            {state === "reconnecting" && <Badge variant="destructive">Reconnecting</Badge>}
          </header>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
