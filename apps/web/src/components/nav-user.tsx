import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { openSettings } from "@/lib/selection"
import { ChevronsUpDownIcon, LogOutIcon, Settings2Icon } from "lucide-react"

export function NavUser({ onSignOut }: { onSignOut: () => void }) {
  const { isMobile, setOpenMobile } = useSidebar()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger render={<SidebarMenuButton size="lg" />}>
            <Avatar>
              <AvatarFallback>Y</AvatarFallback>
            </Avatar>
            <span className="flex-1 truncate font-medium">You</span>
            <ChevronsUpDownIcon className="ml-auto" />
          </DropdownMenuTrigger>
          <DropdownMenuContent side={isMobile ? "bottom" : "right"} align="end" sideOffset={4}>
            <DropdownMenuGroup>
              <DropdownMenuItem
                onClick={() => {
                  setOpenMobile(false)
                  openSettings()
                }}
              >
                <Settings2Icon />
                Settings
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onSignOut}>
                <LogOutIcon />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
