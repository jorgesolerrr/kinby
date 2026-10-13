import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import {
  LIBRARY_PATH,
  openLibrary,
  openThreads,
  openUsage,
  threadsPath,
  USAGE_PATH,
} from "@/lib/selection"
import { ChartColumnIcon, LibraryIcon, MessagesSquareIcon } from "lucide-react"

/**
 * The hub's own pages, above the instances, and the selected instance's thread list. With no
 * instance selected, Threads has nothing to open.
 */
export function NavMain({
  usageOpen,
  libraryOpen,
  threadsOpen,
  instanceId,
}: {
  usageOpen: boolean
  libraryOpen: boolean
  threadsOpen: boolean
  instanceId: string | undefined
}) {
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
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="Library"
            isActive={libraryOpen}
            aria-current={libraryOpen ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openLibrary()
            }}
            render={
              <a href={LIBRARY_PATH}>
                <LibraryIcon />
                <span>Library</span>
              </a>
            }
          />
        </SidebarMenuItem>
        <SidebarMenuItem>
          {instanceId === undefined ? (
            <SidebarMenuButton tooltip="Threads" disabled>
              <MessagesSquareIcon />
              <span>Threads</span>
            </SidebarMenuButton>
          ) : (
            <SidebarMenuButton
              tooltip="Threads"
              isActive={threadsOpen}
              aria-current={threadsOpen ? "page" : undefined}
              onClick={(event) => {
                event.preventDefault()
                openThreads(instanceId)
              }}
              render={
                <a href={threadsPath(instanceId)}>
                  <MessagesSquareIcon />
                  <span>Threads</span>
                </a>
              }
            />
          )}
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}
