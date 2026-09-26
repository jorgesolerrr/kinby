// PROTOTYPE, throwaway. Three variants of the create-instance flow, switchable via `?variant=`,
// inside the real sidebar shell with stub instances. No hub: every call lands in the state panel.
import { useState } from "react"

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { BotIcon, PlusIcon, SparklesIcon } from "lucide-react"

import { PrototypeSwitcher } from "../prototype-switcher"
import { type Flow, useFlow } from "./flow"
import { InstanceAvatar, StatePanel } from "./parts"
import { EXISTING_INSTANCES } from "./stub"
import * as A from "./variant-a"
import * as B from "./variant-b"
import * as C from "./variant-c"

const VARIANTS = [
  { key: "A", name: A.name, Component: A.VariantA },
  { key: "B", name: B.name, Component: B.VariantB },
  { key: "C", name: C.name, Component: C.VariantC },
]

function currentVariant(): string {
  return new URLSearchParams(window.location.search).get("variant") ?? "A"
}

export function CreateInstancePrototype() {
  const [variant, setVariant] = useState(currentVariant)
  const flow = useFlow()
  const { Component } = VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0]

  const change = (key: string) => {
    window.history.replaceState(null, "", `?variant=${key}`)
    setVariant(key)
    flow.reset()
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        <StubSidebar flow={flow} />
        <SidebarInset>
          <header className="flex h-12 items-center gap-2 px-2">
            <SidebarTrigger />
          </header>
          <Component key={variant} flow={flow} />
        </SidebarInset>
      </SidebarProvider>
      <StatePanel flow={flow} />
      <PrototypeSwitcher variants={VARIANTS} current={variant} onChange={change} />
    </TooltipProvider>
  )
}

const sidebarBadge: Record<string, string | null> = {
  choosing: null,
  details: null,
  preparing: "preparing",
  failed: "failed",
  setup: "setup",
  ready: "stopped",
  starting: "starting",
  chat: "running",
}

function StubSidebar({ flow }: { flow: Flow }) {
  const { state } = flow
  const badge = sidebarBadge[state.stage]
  return (
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
          <SidebarGroupLabel>Instances</SidebarGroupLabel>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton isActive={badge === null} onClick={flow.reset}>
                <PlusIcon />
                <span>New instance</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            {badge !== null && (
              <SidebarMenuItem>
                <SidebarMenuButton isActive>
                  <InstanceAvatar
                    name={state.name}
                    shape={state.shape}
                    color={state.color}
                    size="size-4 text-[0.5rem]"
                  />
                  <span>{state.name}</span>
                </SidebarMenuButton>
                <SidebarMenuBadge>{badge}</SidebarMenuBadge>
              </SidebarMenuItem>
            )}
            {EXISTING_INSTANCES.map((instance) => (
              <SidebarMenuItem key={instance.instance_id}>
                <SidebarMenuButton>
                  <BotIcon />
                  <span>{instance.persona_name}</span>
                </SidebarMenuButton>
                <SidebarMenuBadge>{instance.intended_state}</SidebarMenuBadge>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  )
}
