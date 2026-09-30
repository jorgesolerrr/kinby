import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { openUsage, USAGE_PATH } from "@/lib/selection"
import { ChartColumnIcon, MessagesSquareIcon } from "lucide-react"

/** The hub's own pages, above the instances. */
export function NavMain({ usageOpen }: { usageOpen: boolean }) {
  return (
    <SidebarGroup>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="Usage"
            isActive={usageOpen}
            aria-current={usageOpen ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openUsage()
            }}
            render={
              <a href={USAGE_PATH}>
                <ChartColumnIcon />
                <span>Usage</span>
              </a>
            }
          />
        </SidebarMenuItem>
        {/* A placeholder until the flow that owns it lands. */}
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="Threads">
            <MessagesSquareIcon />
            <span>Threads</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}
