import type * as React from "react"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function NavMain({ entries }: { entries: { title: string; icon: React.ReactNode }[] }) {
  return (
    <SidebarGroup>
      <SidebarMenu>
        {entries.map((entry) => (
          <SidebarMenuItem key={entry.title}>
            <SidebarMenuButton tooltip={entry.title}>
              {entry.icon}
              <span>{entry.title}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
}
