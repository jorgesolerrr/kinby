// PROTOTYPE · Variant B, "Rail + column". Today's two columns, tightened into one sidebar: a slim
// rail of instance avatars (home first, an ember dot when one waits on you), and beside it the
// picked instance's column: its name with its pages, New thread, a search, its views, and its
// threads by day. Library and the hub pages sit at the rail's foot and open as full pages, with a
// column of hub sections in place of the threads. On mobile, rail and column slide in together.
import {
  BarChart3Icon,
  BrainIcon,
  ChevronDownIcon,
  CogIcon,
  HistoryIcon,
  HomeIcon,
  LayoutPanelLeftIcon,
  LibraryIcon,
  PlusIcon,
  SearchIcon,
  Settings2Icon,
  SquarePenIcon,
} from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInput,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "cn"

import {
  HUB_PAGES,
  INSTANCES,
  type InstanceTab,
  type MockInstance,
  type Screen,
  instanceById,
  needsYou,
  waiting,
} from "./mock"
import { type Go, MainScreen, StatusIcon } from "./screens"

const PAGES: { id: InstanceTab; label: string; icon: React.ComponentType }[] = [
  { id: "overview", label: "Overview", icon: HomeIcon },
  { id: "config", label: "Config", icon: CogIcon },
  { id: "routines", label: "Routines", icon: HistoryIcon },
  { id: "memory", label: "Memory", icon: BrainIcon },
  { id: "stats", label: "Stats", icon: BarChart3Icon },
]

export function VariantB({ screen, go }: { screen: Screen; go: Go }) {
  const home = INSTANCES.find((i) => i.home) ?? INSTANCES[0]
  // The rail's pick outlives screens that name no instance, as a selected server does in Slack.
  const [picked, setPicked] = useState(home.id)
  const instanceId = "instanceId" in screen ? screen.instanceId : picked
  const hub = screen.kind === "hub" || screen.kind === "library"
  return (
    <SidebarProvider style={{ "--sidebar-width": "20rem" } as React.CSSProperties}>
      <Sidebar collapsible="offcanvas">
        <div className="flex h-full min-h-0 flex-row">
          <Rail
            instanceId={hub ? undefined : instanceId}
            screen={screen}
            go={go}
            onPick={(id) => {
              setPicked(id)
              go({ kind: "home" })
            }}
            homeId={home.id}
          />
          <div className="flex min-w-0 flex-1 flex-col">
            {hub ? (
              <HubColumn screen={screen} go={go} />
            ) : (
              <ThreadsColumn instance={instanceById(instanceId)} screen={screen} go={go} />
            )}
          </div>
        </div>
      </Sidebar>
      <SidebarInset className="h-svh min-w-0">
        <header className="flex h-12 shrink-0 items-center gap-2 px-3">
          <SidebarTrigger />
        </header>
        <MainScreen screen={screen} go={go} homeId={instanceId} />
      </SidebarInset>
    </SidebarProvider>
  )
}

function Rail({
  instanceId,
  screen,
  go,
  onPick,
  homeId,
}: {
  instanceId: string | undefined
  screen: Screen
  go: Go
  onPick: (id: string) => void
  homeId: string
}) {
  return (
    <nav
      aria-label="Instances"
      className="flex w-14 shrink-0 flex-col items-center gap-2 border-r py-3"
    >
      <KinbyMark className="mb-1 size-7" />
      {INSTANCES.map((instance, n) => (
        <div key={instance.id} className="contents">
          {n === 1 && <Separator className="w-6" />}
          <RailButton
            label={`${instance.name}${instance.id === homeId ? " (home)" : ""}`}
            active={instance.id === instanceId}
            onClick={() => onPick(instance.id)}
          >
            <span className={cn("relative", instance.state === "stopped" && "opacity-50")}>
              <InstanceAvatar avatar={instance.avatar} name={instance.name} />
              {waiting(instance) > 0 && (
                <span className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full bg-ember ring-2 ring-sidebar" />
              )}
            </span>
          </RailButton>
        </div>
      ))}
      <RailButton
        label="New instance"
        active={screen.kind === "new-instance"}
        onClick={() => go({ kind: "new-instance" })}
      >
        <PlusIcon />
      </RailButton>
      <div className="mt-auto flex flex-col items-center gap-2">
        <RailButton
          label="Library"
          active={screen.kind === "library"}
          onClick={() => go({ kind: "library" })}
        >
          <LibraryIcon />
        </RailButton>
        <RailButton
          label="Settings"
          active={screen.kind === "hub"}
          onClick={() => go({ kind: "hub", page: "general" })}
        >
          <Settings2Icon />
        </RailButton>
        <Avatar size="sm">
          <AvatarFallback>J</AvatarFallback>
        </Avatar>
      </div>
    </nav>
  )
}

function RailButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <SidebarMenuButton
            isActive={active}
            aria-label={label}
            className="size-10! justify-center p-0!"
            onClick={onClick}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )
}

type Shown = "all" | "needs" | "routines"

function ThreadsColumn({
  instance,
  screen,
  go,
}: {
  instance: MockInstance
  screen: Screen
  go: Go
}) {
  const { setOpenMobile } = useSidebar()
  const [shown, setShown] = useState<Shown>("all")
  const [query, setQuery] = useState("")
  const open = (to: Screen) => {
    go(to)
    setOpenMobile(false)
  }
  const threads = instance.threads
    .filter((t) =>
      shown === "needs" ? needsYou(t) : shown === "routines" ? t.routine : !t.routine,
    )
    .filter((t) => t.title.toLowerCase().includes(query.toLowerCase()))
  const days = ["Today", "Yesterday", "Earlier"] as const
  return (
    <>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger render={<SidebarMenuButton size="lg" />}>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-heading text-base font-semibold">
                    {instance.name}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {instance.home ? "Home instance · " : ""}
                    {instance.state}
                  </span>
                </span>
                <ChevronDownIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  {PAGES.map((p) => (
                    <DropdownMenuItem
                      key={p.id}
                      onClick={() => open({ kind: "instance", instanceId: instance.id, tab: p.id })}
                    >
                      <p.icon />
                      {p.label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={screen.kind === "home"}
              onClick={() => open({ kind: "home" })}
            >
              <SquarePenIcon />
              <span>New thread</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <div className="relative">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
          <SidebarInput
            placeholder="Search threads"
            className="pl-8"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <ToggleGroup
          value={[shown]}
          onValueChange={(v: string[]) => v[0] && setShown(v[0] as Shown)}
          size="sm"
          variant="outline"
          className="w-full"
        >
          <ToggleGroupItem value="all" className="flex-1">
            All
          </ToggleGroupItem>
          <ToggleGroupItem value="needs" className="flex-1">
            Needs you
          </ToggleGroupItem>
          <ToggleGroupItem value="routines" className="flex-1">
            Routines
          </ToggleGroupItem>
        </ToggleGroup>
      </SidebarHeader>
      <SidebarContent>
        {instance.views.length > 0 && (
          <SidebarGroup>
            <SidebarGroupLabel>Views</SidebarGroupLabel>
            <SidebarMenu>
              {instance.views.map((v) => (
                <SidebarMenuItem key={v.id}>
                  <SidebarMenuButton
                    isActive={screen.kind === "view" && screen.viewId === v.id}
                    onClick={() => open({ kind: "view", instanceId: instance.id, viewId: v.id })}
                  >
                    <LayoutPanelLeftIcon />
                    <span>{v.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        )}
        {days.map((day) => {
          const inDay = threads.filter((t) => t.day === day)
          if (inDay.length === 0) return null
          return (
            <SidebarGroup key={day}>
              <SidebarGroupLabel>{day}</SidebarGroupLabel>
              <SidebarMenu>
                {inDay.map((t) => (
                  <SidebarMenuItem key={t.id}>
                    <SidebarMenuButton
                      isActive={screen.kind === "thread" && screen.threadId === t.id}
                      onClick={() =>
                        open({ kind: "thread", instanceId: instance.id, threadId: t.id })
                      }
                    >
                      <span className="truncate">{t.title}</span>
                      <StatusIcon status={t.status} />
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroup>
          )
        })}
      </SidebarContent>
    </>
  )
}

/** In place of the threads, while Library or a hub page is open: the hub's sections. */
function HubColumn({ screen, go }: { screen: Screen; go: Go }) {
  const { setOpenMobile } = useSidebar()
  const open = (to: Screen) => {
    go(to)
    setOpenMobile(false)
  }
  return (
    <>
      <SidebarHeader>
        <div className="px-2 py-1.5 font-heading text-base font-semibold">Hub</div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                isActive={screen.kind === "library"}
                onClick={() => open({ kind: "library" })}
              >
                <LibraryIcon />
                <span>Library</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Settings</SidebarGroupLabel>
          <SidebarMenu>
            {HUB_PAGES.map((p) => (
              <SidebarMenuItem key={p.id}>
                <SidebarMenuButton
                  isActive={screen.kind === "hub" && screen.page === p.id}
                  onClick={() => open({ kind: "hub", page: p.id })}
                >
                  <span>{p.label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
    </>
  )
}
