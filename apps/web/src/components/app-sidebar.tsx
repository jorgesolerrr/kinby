import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { NavInstances } from "@/components/nav-instances"
import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { openHome } from "@/lib/selection"

export function AppSidebar({
  instances,
  selected,
  creating,
  anyRemoved,
  removedOpen,
  usageOpen,
  libraryOpen,
  secretsOpen,
  client,
  clock,
  selectedClient,
  onSignOut,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  /** Whether the hub has a removed instance, to link to the Removed instances page. */
  anyRemoved: boolean
  removedOpen: boolean
  usageOpen: boolean
  libraryOpen: boolean
  secretsOpen: boolean
  /** Reaches the instances that are not selected, to count their threads that need the user. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's connection, whose threads column lists the threads its row counts. */
  selectedClient: Pick<InstanceClient, "call"> | undefined
  onSignOut: () => void
}) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              tooltip="kinby"
              onClick={(event) => {
                event.preventDefault()
                openHome()
              }}
              render={
                <a href="/">
                  <span className="flex size-8 shrink-0 items-center justify-center">
                    <KinbyMark className="size-7!" />
                  </span>
                  <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
                </a>
              }
            />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavInstances
          instances={instances}
          selected={selected}
          creating={creating}
          anyRemoved={anyRemoved}
          removedOpen={removedOpen}
          client={client}
          clock={clock}
          selectedClient={selectedClient}
        />
      </SidebarContent>
      <SidebarFooter>
        <NavMain usageOpen={usageOpen} libraryOpen={libraryOpen} secretsOpen={secretsOpen} />
        <NavUser onSignOut={onSignOut} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
