import {
  Button,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@kinby/web"
import {
  ChevronDownIcon,
  CopyIcon,
  LogOutIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlayIcon,
  Trash2Icon,
} from "lucide-react"

export const ThreadActions = () => (
  <div className="flex justify-center p-6">
    <DropdownMenu open>
      <DropdownMenuTrigger render={<Button variant="outline" />}>
        Thread
        <ChevronDownIcon data-icon="inline-end" />
      </DropdownMenuTrigger>
      <DropdownMenuContent style={{ width: "14rem" }}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>Plan the offsite</DropdownMenuLabel>
          <DropdownMenuItem>
            <PencilIcon />
            Rename
            <DropdownMenuShortcut>⌘R</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem>
            <CopyIcon />
            Duplicate
            <DropdownMenuShortcut>⌘D</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem>
            <PlayIcon />
            Continue in another instance
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem variant="destructive">
            <Trash2Icon />
            Delete thread
            <DropdownMenuShortcut>⌫</DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  </div>
)

export const ThemeAndAccount = () => (
  <div className="flex justify-center p-6">
    <DropdownMenu open>
      <DropdownMenuTrigger render={<Button variant="ghost" />}>You</DropdownMenuTrigger>
      <DropdownMenuContent className="w-48">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Theme</DropdownMenuLabel>
          <DropdownMenuRadioGroup value="dark">
            <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem>
            <LogOutIcon />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  </div>
)

export const ColumnToggles = () => (
  <div className="flex justify-center p-6">
    <DropdownMenu open>
      <DropdownMenuTrigger render={<Button variant="outline" size="icon" aria-label="Columns" />}>
        <MoreHorizontalIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent style={{ width: "13rem" }}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>Show in routines</DropdownMenuLabel>
          <DropdownMenuCheckboxItem checked>Schedule</DropdownMenuCheckboxItem>
          <DropdownMenuCheckboxItem checked>Last run</DropdownMenuCheckboxItem>
          <DropdownMenuCheckboxItem>Instance</DropdownMenuCheckboxItem>
          <DropdownMenuCheckboxItem disabled>Cost per run</DropdownMenuCheckboxItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  </div>
)
