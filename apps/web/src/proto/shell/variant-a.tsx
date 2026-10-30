// PROTOTYPE · Variant A, "One sidebar". Claude Code's shape: one sidebar whose top band holds New
// thread, Search and Library, whose body groups each instance's threads under it (home instance
// first), and whose foot holds the account. Hub admin lives in a modal Settings dialog with a left
// list of sections. An instance's own surfaces are tabs on its page, opened from its row.
import {
  ChevronRightIcon,
  ChevronsUpDownIcon,
  LayoutPanelLeftIcon,
  LibraryIcon,
  LogOutIcon,
  MoreHorizontalIcon,
  PlusIcon,
  SearchIcon,
  Settings2Icon,
  SquarePenIcon,
} from "lucide-react"
import type * as React from "react"
import { useEffect, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import {
  HUB_PAGES,
  type HubPage,
  INSTANCES,
  INSTANCE_TABS,
  type MockInstance,
  type Screen,
  instanceById,
  waiting,
} from "./mock"
import { type Go, HubContent, MainScreen, StatusIcon } from "./screens"

/** How many recent threads a group shows before "All threads". */
const RECENT = 4

export function VariantA({ screen, go }: { screen: Screen; go: Go }) {
  const [settings, setSettings] = useState<HubPage | undefined>()
  const [search, setSearch] = useState(false)
  const home = INSTANCES.find((i) => i.home) ?? INSTANCES[0]

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        setSearch((s) => !s)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  return (
    <SidebarProvider>
      <Sidebar collapsible="offcanvas">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem className="flex items-center">
              <SidebarMenuButton size="lg" onClick={() => go({ kind: "home" })}>
                <KinbyMark className="size-7!" />
                <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <SidebarMenu>
            <SidebarMenuItem>
              <Nav go={go} to={{ kind: "home" }} active={screen.kind === "home"}>
                <SquarePenIcon />
                <span>New thread</span>
              </Nav>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={() => setSearch(true)}>
                <SearchIcon />
                <span>Search</span>
                <kbd className="ml-auto font-mono text-xs text-muted-foreground">Ctrl K</kbd>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <Nav go={go} to={{ kind: "library" }} active={screen.kind === "library"}>
                <LibraryIcon />
                <span>Library</span>
              </Nav>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Instances</SidebarGroupLabel>
            <SidebarMenu>
              {INSTANCES.map((instance) => (
                <InstanceGroup key={instance.id} instance={instance} screen={screen} go={go} />
              ))}
              <SidebarMenuItem>
                <Nav go={go} to={{ kind: "new-instance" }} active={screen.kind === "new-instance"}>
                  <PlusIcon />
                  <span>New instance</span>
                </Nav>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <Account onSettings={() => setSettings("general")} />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="h-svh min-w-0">
        <header className="flex h-12 shrink-0 items-center gap-2 px-3">
          <SidebarTrigger />
          <Title screen={screen} />
        </header>
        <MainScreen screen={screen} go={go} instanceTabs homeId={home.id} pick />
      </SidebarInset>

      <SettingsDialog page={settings} onPage={setSettings} />
      <CommandDialog open={search} onOpenChange={setSearch} title="Search threads">
        <CommandInput placeholder="Search threads…" />
        <CommandList>
          <CommandEmpty>No thread matches.</CommandEmpty>
          {INSTANCES.map((instance) => (
            <CommandGroup key={instance.id} heading={instance.name}>
              {instance.threads.map((t) => (
                <CommandItem
                  key={t.id}
                  onSelect={() => {
                    setSearch(false)
                    go({ kind: "thread", instanceId: instance.id, threadId: t.id })
                  }}
                >
                  {t.title}
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </CommandDialog>
    </SidebarProvider>
  )
}

/** A sidebar button that opens a screen and closes the mobile sheet. */
function Nav({
  go,
  to,
  active,
  children,
  size,
}: {
  go: Go
  to: Screen
  active: boolean
  children: React.ReactNode
  size?: "default" | "lg"
}) {
  const { setOpenMobile } = useSidebar()
  return (
    <SidebarMenuButton
      size={size}
      isActive={active}
      onClick={() => {
        go(to)
        setOpenMobile(false)
      }}
    >
      {children}
    </SidebarMenuButton>
  )
}

/** An instance and, folded under it, its views and its most recent threads. */
function InstanceGroup({
  instance,
  screen,
  go,
}: {
  instance: MockInstance
  screen: Screen
  go: Go
}) {
  const { setOpenMobile } = useSidebar()
  const count = waiting(instance)
  const here = "instanceId" in screen && screen.instanceId === instance.id
  const [open, setOpen] = useState(instance.home === true || count > 0)
  const open_ = (to: Screen) => {
    go(to)
    setOpenMobile(false)
  }
  return (
    <Collapsible open={open} onOpenChange={setOpen} render={<SidebarMenuItem />}>
      <CollapsibleTrigger
        render={<SidebarMenuButton isActive={here && screen.kind === "instance"} />}
      >
        <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
        <span className="truncate font-medium">{instance.name}</span>
        {instance.state === "stopped" && (
          <span className="text-xs text-muted-foreground">stopped</span>
        )}
        {count > 0 && (
          <span
            aria-label={`${count} waiting for you`}
            className="ml-auto size-2 shrink-0 rounded-full bg-ember"
          />
        )}
        <ChevronRightIcon
          className={`transition-transform ${count > 0 ? "" : "ml-auto"} ${open ? "rotate-90" : ""}`}
        />
      </CollapsibleTrigger>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<SidebarMenuAction showOnHover aria-label={`${instance.name} menu`} />}
        >
          <MoreHorizontalIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start">
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={() => open_({ kind: "home" })}>
              <SquarePenIcon />
              New thread with {instance.name}
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {INSTANCE_TABS.map((t) => (
              <DropdownMenuItem
                key={t.id}
                onClick={() => open_({ kind: "instance", instanceId: instance.id, tab: t.id })}
              >
                {t.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <CollapsibleContent>
        <SidebarMenuSub>
          {instance.views.map((v) => (
            <SidebarMenuSubItem key={v.id}>
              <SidebarMenuSubButton
                isActive={screen.kind === "view" && screen.viewId === v.id}
                onClick={() => open_({ kind: "view", instanceId: instance.id, viewId: v.id })}
              >
                <LayoutPanelLeftIcon />
                <span>{v.title}</span>
              </SidebarMenuSubButton>
            </SidebarMenuSubItem>
          ))}
          {instance.threads.slice(0, RECENT).map((t) => (
            <SidebarMenuSubItem key={t.id}>
              <SidebarMenuSubButton
                isActive={screen.kind === "thread" && screen.threadId === t.id}
                onClick={() => open_({ kind: "thread", instanceId: instance.id, threadId: t.id })}
              >
                <span className="truncate">{t.title}</span>
                <StatusIcon status={t.status} />
              </SidebarMenuSubButton>
            </SidebarMenuSubItem>
          ))}
          {instance.threads.length > RECENT && (
            <SidebarMenuSubItem>
              <SidebarMenuSubButton
                className="text-muted-foreground"
                isActive={screen.kind === "threads" && here}
                onClick={() => open_({ kind: "threads", instanceId: instance.id })}
              >
                All threads
              </SidebarMenuSubButton>
            </SidebarMenuSubItem>
          )}
        </SidebarMenuSub>
      </CollapsibleContent>
    </Collapsible>
  )
}

function Account({ onSettings }: { onSettings: () => void }) {
  const { isMobile, setOpenMobile } = useSidebar()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger render={<SidebarMenuButton size="lg" />}>
            <Avatar>
              <AvatarFallback>J</AvatarFallback>
            </Avatar>
            <span className="flex-1 truncate font-medium">Jorge</span>
            <ChevronsUpDownIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent side={isMobile ? "top" : "right"} align="end">
            <DropdownMenuGroup>
              <DropdownMenuItem
                onClick={() => {
                  setOpenMobile(false)
                  onSettings()
                }}
              >
                <Settings2Icon />
                Settings
              </DropdownMenuItem>
              <DropdownMenuItem>
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

/** Hub admin as one modal surface, a left list of sections and the section's body on the right. */
function SettingsDialog({
  page,
  onPage,
}: {
  page: HubPage | undefined
  onPage: (p: HubPage | undefined) => void
}) {
  return (
    <Dialog open={page !== undefined} onOpenChange={(open) => !open && onPage(undefined)}>
      <DialogContent className="h-[min(640px,90svh)] max-w-[calc(100%-2rem)] p-0 sm:max-w-4xl">
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">The hub's settings</DialogDescription>
        <Tabs
          value={page ?? "general"}
          onValueChange={(v: HubPage) => onPage(v)}
          orientation="vertical"
          className="min-h-0 flex-1 flex-col md:flex-row"
        >
          <div className="flex shrink-0 flex-col gap-3 border-b p-4 md:w-52 md:border-r md:border-b-0">
            <h2 className="font-heading text-base font-semibold">Settings</h2>
            <TabsList
              variant="line"
              className="w-full items-stretch max-md:flex-row max-md:overflow-x-auto"
            >
              {HUB_PAGES.map((p) => (
                <TabsTrigger key={p.id} value={p.id} className="justify-start">
                  {p.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          {HUB_PAGES.map((p) => (
            <TabsContent key={p.id} value={p.id} className="min-h-0 overflow-y-auto p-6">
              <h3 className="mb-4 font-heading text-base font-semibold">{p.label}</h3>
              <HubContent page={p.id} />
            </TabsContent>
          ))}
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function Title({ screen }: { screen: Screen }) {
  if (screen.kind === "thread") {
    const instance = instanceById(screen.instanceId)
    const thread = instance.threads.find((t) => t.id === screen.threadId)
    return (
      <span className="flex min-w-0 items-center gap-2 text-sm">
        <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
        <span className="text-muted-foreground">{instance.name}</span>
        <span className="text-muted-foreground">/</span>
        <span className="truncate font-medium">{thread?.title}</span>
      </span>
    )
  }
  return null
}
