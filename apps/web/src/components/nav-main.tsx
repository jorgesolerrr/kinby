import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { LIBRARY_PATH, openLibrary } from "@/lib/selection"
import { LibraryIcon } from "lucide-react"

/** The hub's library, at the sidebar's foot above the user. */
export function NavMain({ libraryOpen }: { libraryOpen: boolean }) {
  return (
    <SidebarGroup>
      <SidebarMenu>
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
      </SidebarMenu>
    </SidebarGroup>
  )
}
