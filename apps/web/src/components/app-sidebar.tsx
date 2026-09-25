import type { InstanceSummary } from "@kinby/contract"
import type * as React from "react"

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
import { ChartColumnIcon, MessagesSquareIcon, SparklesIcon } from "lucide-react"

// Placeholders until the flows that own them land.
const entries = [
  { title: "Threads", icon: <MessagesSquareIcon /> },
  { title: "Usage", icon: <ChartColumnIcon /> },
]

export function AppSidebar({
  instances,
  selected,
  onSignOut,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  onSignOut: () => void
}) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg">
              <SparklesIcon />
              <span className="font-medium">kinby</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain entries={entries} />
        <NavInstances instances={instances} selected={selected} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser onSignOut={onSignOut} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
