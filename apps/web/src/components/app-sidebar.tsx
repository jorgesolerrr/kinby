import type { Client, Clock, InstanceSummary } from "@kinby/contract"
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

export function AppSidebar({
  instances,
  selected,
  creating,
  anyRemoved,
  removedOpen,
  usageOpen,
  libraryOpen,
  secretsOpen,
  threadsOpen,
  client,
  clock,
  threads,
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
  threadsOpen: boolean
  /** Reaches the instances that are not selected, to count their threads that need the user. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's threads, when it has them to show. */
  threads: React.ReactNode
  onSignOut: () => void
}) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg">
              <KinbyMark />
              <span className="font-heading text-base font-semibold">kinby</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain
          usageOpen={usageOpen}
          libraryOpen={libraryOpen}
          secretsOpen={secretsOpen}
          threadsOpen={threadsOpen}
          instanceId={selected?.instance_id}
        />
        <NavInstances
          instances={instances}
          selected={selected}
          creating={creating}
          anyRemoved={anyRemoved}
          removedOpen={removedOpen}
          client={client}
          clock={clock}
          threads={threads}
        />
      </SidebarContent>
      <SidebarFooter>
        <NavUser onSignOut={onSignOut} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
