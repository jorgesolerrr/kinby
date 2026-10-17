// PROTOTYPE layout A, "Sidebar": today's shell with the approved components. One collapsible
// sidebar holds the hub's pages, the instances, and the selected instance's threads nested
// under it; the thread fills the rest.
import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar"

import { instances, threads } from "./fixtures"
import { HUB_PAGES, ThreadView } from "./layout-parts"
import { initial, STATE } from "./sheet"

export const name = "Sidebar (today's shell)"

export function LayoutA() {
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                size="lg"
                tooltip="kinby"
                render={
                  <a href="#home">
                    <span className="flex size-8 shrink-0 items-center justify-center">
                      <KinbyMark className="size-7!" />
                    </span>
                    <span className="font-heading text-lg font-semibold">kinby</span>
                  </a>
                }
              />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarMenu>
              {HUB_PAGES.map((page) => (
                <SidebarMenuItem key={page.label}>
                  <SidebarMenuButton
                    tooltip={page.label}
                    render={
                      <a href="#page">
                        <page.icon />
                        <span>{page.label}</span>
                      </a>
                    }
                  />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>Instances</SidebarGroupLabel>
            <SidebarMenu>
              {instances.map((instance, index) => (
                <SidebarMenuItem key={instance.id}>
                  <SidebarMenuButton
                    size="lg"
                    tooltip={instance.name}
                    isActive={index === 0}
                    render={
                      <a href="#instance" aria-current={index === 0 ? "page" : undefined}>
                        <Avatar shape={instance.avatar.shape} size="sm" aria-hidden>
                          <AvatarFallback variant={instance.avatar.color}>
                            {initial(instance.name)}
                          </AvatarFallback>
                        </Avatar>
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate font-medium">{instance.name}</span>
                          <span className="truncate font-mono text-xs text-muted-foreground">
                            {instance.needsYou > 0
                              ? `${instance.needsYou} waiting for you`
                              : instance.state === "running"
                                ? instance.role
                                : STATE[instance.state].label}
                          </span>
                        </span>
                        {instance.needsYou > 0 && (
                          <span aria-hidden className="size-2 shrink-0 rounded-full bg-ember" />
                        )}
                      </a>
                    }
                  />
                  {index === 0 && (
                    <SidebarMenuSub>
                      {threads.map((thread, threadIndex) => (
                        <SidebarMenuSubItem key={thread.id}>
                          <SidebarMenuSubButton
                            isActive={threadIndex === 0}
                            render={
                              <a href="#thread">
                                <span className="truncate">{thread.title}</span>
                              </a>
                            }
                          />
                        </SidebarMenuSubItem>
                      ))}
                    </SidebarMenuSub>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="h-svh">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <SidebarTrigger />
          <h1 className="truncate text-sm font-semibold">Dentist and Ana</h1>
          <span className="font-mono text-xs text-muted-foreground">atlas · 2 turns</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-24">
          <ThreadView />
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
