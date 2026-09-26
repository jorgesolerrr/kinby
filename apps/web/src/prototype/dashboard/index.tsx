// PROTOTYPE, throwaway. Three variants of the dashboard flow (hub Usage page and instance Stats
// tab), switchable via `?variant=`, inside the sidebar shell with a stub hub of four instances.
import { useState } from "react"

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
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { BarChart3Icon, BotIcon, SparklesIcon } from "lucide-react"

import { PrototypeSwitcher } from "../prototype-switcher"
import { useDashboard } from "./flow"
import { StatePanel } from "./parts"
import { INSTANCES } from "./stub"
import * as A from "./variant-a"
import * as B from "./variant-b"
import * as C from "./variant-c"

const VARIANTS = [
  { key: "A", name: A.name, Stats: A.InstanceStats, Usage: A.HubUsage },
  { key: "B", name: B.name, Stats: B.InstanceStats, Usage: B.HubUsage },
  { key: "C", name: C.name, Stats: C.InstanceStats, Usage: C.HubUsage },
]

function currentVariant(): string {
  return new URLSearchParams(window.location.search).get("variant") ?? "B"
}

export function DashboardPrototype() {
  const [variant, setVariant] = useState(currentVariant)
  const d = useDashboard()
  const { Stats, Usage } = VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0]

  const change = (key: string) => {
    window.history.replaceState(null, "", `?variant=${key}`)
    setVariant(key)
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar collapsible="icon">
          <SidebarHeader>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton size="lg">
                  <SparklesIcon />
                  <span className="font-medium">kinby</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton isActive={d.page.kind === "usage"} onClick={() => d.open({ kind: "usage" })}>
                    <BarChart3Icon />
                    <span>Usage</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
            <SidebarGroup>
              <SidebarGroupLabel>Instances</SidebarGroupLabel>
              <SidebarMenu>
                {INSTANCES.map((i) => {
                  const here = d.page.kind === "stats" && d.page.instanceId === i.id
                  return (
                    <SidebarMenuItem key={i.id}>
                      <SidebarMenuButton isActive={here} disabled={i.state !== "running"}>
                        <BotIcon />
                        <span>{i.name}</span>
                        {i.state !== "running" && <span className="ml-auto text-xs">{i.state}</span>}
                      </SidebarMenuButton>
                      {i.state === "running" && (
                        <SidebarMenuSub>
                          <SidebarMenuSubItem>
                            <SidebarMenuSubButton>Threads…</SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                          <SidebarMenuSubItem>
                            <SidebarMenuSubButton isActive={here} onClick={() => d.open({ kind: "stats", instanceId: i.id })}>
                              Stats
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                        </SidebarMenuSub>
                      )}
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>
        <SidebarInset className="h-svh">
          <header className="flex h-12 shrink-0 items-center gap-2 px-2">
            <SidebarTrigger />
          </header>
          {d.page.kind === "usage" ? <Usage key={variant} d={d} /> : <Stats key={variant} d={d} />}
        </SidebarInset>
      </SidebarProvider>
      <StatePanel d={d} />
      <PrototypeSwitcher variants={VARIANTS} current={variant} onChange={change} />
    </TooltipProvider>
  )
}
