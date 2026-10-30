import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import { LibraryIcon, SquarePenIcon } from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { type Folds, NavInstances } from "@/components/nav-instances"
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
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { homePath, LIBRARY_PATH } from "@/lib/selection"

export function AppSidebar({
  instances,
  selected,
  creating,
  libraryOpen,
  client,
  clock,
  selectedClient,
  onSignOut,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  libraryOpen: boolean
  /** Reaches the instances that are not selected, to list their threads and views. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's connection, whose thread list the open thread reads and changes. */
  selectedClient: Pick<InstanceClient, "call" | "state" | "onStateChange"> | undefined
  onSignOut: () => void
}) {
  const open = useOpenFromSidebar()
  const [folds, setFolds] = useState<Folds>({})
  return (
    <Sidebar collapsible="offcanvas" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              onClick={(event) => {
                event.preventDefault()
                open(homePath())
              }}
              render={
                <a href={homePath()}>
                  <span className="flex size-8 shrink-0 items-center justify-center">
                    <KinbyMark className="size-7!" />
                  </span>
                  <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
                </a>
              }
            />
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={(event) => {
                event.preventDefault()
                open(homePath())
              }}
              render={
                <a href={homePath()}>
                  <SquarePenIcon />
                  <span>New thread</span>
                </a>
              }
            />
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={libraryOpen}
              aria-current={libraryOpen ? "page" : undefined}
              onClick={(event) => {
                event.preventDefault()
                open(LIBRARY_PATH)
              }}
              render={
                <a href={LIBRARY_PATH}>
                  <LibraryIcon />
                  <span>Library</span>
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
          client={client}
          clock={clock}
          selectedClient={selectedClient}
          folds={folds}
          onFold={(instanceId, opened) => setFolds((kept) => ({ ...kept, [instanceId]: opened }))}
        />
      </SidebarContent>
      <SidebarFooter>
        <NavUser onSignOut={onSignOut} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
