import {
  Avatar,
  AvatarFallback,
  Badge,
  KinbyMark,
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
} from "@kinby/web"
import {
  ChartColumnIcon,
  ChevronsUpDownIcon,
  KeyRoundIcon,
  LibraryIcon,
  MessagesSquareIcon,
  PlusIcon,
} from "lucide-react"
import type { ReactNode } from "react"

/**
 * The sidebar's container is `fixed` to the viewport. Inside a preview cell it is pinned to the
 * frame instead: `absolute h-full` replaces `fixed h-svh`, and the provider's `min-h-svh` gives
 * way to a fixed height.
 */
const IN_FRAME = "absolute h-full"

function Frame({
  defaultOpen = true,
  children,
}: {
  defaultOpen?: boolean
  children: ReactNode
}) {
  return (
    <SidebarProvider
      defaultOpen={defaultOpen}
      className="relative min-h-0 overflow-hidden rounded-xl border"
      style={{ height: 600 }}
    >
      {children}
    </SidebarProvider>
  )
}

function Brand() {
  return (
    <SidebarHeader>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" render={<a href="/" />}>
            <span className="flex size-8 shrink-0 items-center justify-center">
              <KinbyMark className="size-7!" />
            </span>
            <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarHeader>
  )
}

function Pages({ active }: { active?: string }) {
  const pages = [
    ["Usage", ChartColumnIcon],
    ["Library", LibraryIcon],
    ["Secrets", KeyRoundIcon],
    ["Threads", MessagesSquareIcon],
  ] as const
  return (
    <SidebarGroup>
      <SidebarMenu>
        {pages.map(([label, Icon]) => (
          <SidebarMenuItem key={label}>
            <SidebarMenuButton isActive={label === active}>
              <Icon />
              <span>{label}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
}

function InstanceAvatar({ name, color }: { name: string; color: "blue" | "violet" | "green" }) {
  return (
    <Avatar shape="squircle" aria-hidden>
      <AvatarFallback variant={color}>{name.charAt(0).toUpperCase()}</AvatarFallback>
    </Avatar>
  )
}

function Instances({ withThreads }: { withThreads: boolean }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" isActive={withThreads}>
            <InstanceAvatar name="home" color="blue" />
            <span>home</span>
          </SidebarMenuButton>
          {withThreads && (
            <>
              <SidebarMenuAction>
                <PlusIcon />
                <span className="sr-only">New thread</span>
              </SidebarMenuAction>
              <SidebarMenuSub>
                <SidebarMenuSubItem>
                  <SidebarMenuSubButton isActive href="#">
                    <span className="min-w-0 truncate">Move the dentist to Thursday</span>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
                <SidebarMenuSubItem>
                  <SidebarMenuSubButton href="#">
                    <span className="min-w-0 truncate">Nightly backup check</span>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
                <SidebarMenuSubItem>
                  <SidebarMenuSubButton href="#">
                    <span className="min-w-0 truncate">Renew kinby.dev</span>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              </SidebarMenuSub>
            </>
          )}
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg">
            <InstanceAvatar name="coder" color="violet" />
            <span>coder</span>
          </SidebarMenuButton>
          <SidebarMenuBadge>
            <Badge variant="attention">2</Badge>
          </SidebarMenuBadge>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg">
            <InstanceAvatar name="finance" color="green" />
            <span>finance</span>
          </SidebarMenuButton>
          <SidebarMenuBadge>
            <span className="font-normal text-muted-foreground">stopped</span>
          </SidebarMenuBadge>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton>
            <PlusIcon />
            <span>New instance</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}

function User() {
  return (
    <SidebarFooter>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg">
            <Avatar>
              <AvatarFallback>Y</AvatarFallback>
            </Avatar>
            <span className="flex-1 truncate font-medium">You</span>
            <ChevronsUpDownIcon className="ml-auto" />
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
  )
}

function Page({ title, children }: { title: string; children: ReactNode }) {
  return (
    <SidebarInset>
      <div className="flex flex-col gap-2 p-6">
        <div className="font-heading text-lg font-semibold">{title}</div>
        <p className="text-sm text-muted-foreground">{children}</p>
      </div>
    </SidebarInset>
  )
}

export const AppSidebar = () => (
  <Frame>
    <Sidebar collapsible="icon" className={IN_FRAME}>
      <Brand />
      <SidebarContent>
        <Pages />
        <Instances withThreads />
      </SidebarContent>
      <User />
    </Sidebar>
    <Page title="Move the dentist to Thursday">
      The thread with home opens here, next to the sidebar.
    </Page>
  </Frame>
)

export const Collapsed = () => (
  <Frame defaultOpen={false}>
    <Sidebar collapsible="icon" className={IN_FRAME}>
      <Brand />
      <SidebarContent>
        <Pages active="Usage" />
        <Instances withThreads={false} />
      </SidebarContent>
      <User />
    </Sidebar>
    <Page title="Usage">Collapsed to its icon rail, the sidebar leaves the page the room.</Page>
  </Frame>
)

export const Floating = () => (
  <Frame>
    <Sidebar variant="floating" className={IN_FRAME}>
      <Brand />
      <SidebarContent>
        <Pages active="Library" />
        <Instances withThreads={false} />
      </SidebarContent>
      <User />
    </Sidebar>
    <Page title="Library">The floating variant sits inset from the edge with a ring.</Page>
  </Frame>
)
