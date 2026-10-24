import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import {
  CHANNELS_PATH,
  LIBRARY_PATH,
  openChannels,
  openLibrary,
  openSecrets,
  openUsage,
  SECRETS_PATH,
  USAGE_PATH,
} from "@/lib/selection"
import { BellIcon, ChartColumnIcon, KeyRoundIcon, LibraryIcon } from "lucide-react"

/** The hub's own pages, at the sidebar's foot above the user. */
export function NavMain({
  usageOpen,
  libraryOpen,
  secretsOpen,
  channelsOpen,
}: {
  usageOpen: boolean
  libraryOpen: boolean
  secretsOpen: boolean
  channelsOpen: boolean
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
          <SidebarMenuButton
            tooltip="Secrets"
            isActive={secretsOpen}
            aria-current={secretsOpen ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openSecrets()
            }}
            render={
              <a href={SECRETS_PATH}>
                <KeyRoundIcon />
                <span>Secrets</span>
              </a>
            }
          />
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="Channels"
            isActive={channelsOpen}
            aria-current={channelsOpen ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openChannels()
            }}
            render={
              <a href={CHANNELS_PATH}>
                <BellIcon />
                <span>Channels</span>
              </a>
            }
          />
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}
