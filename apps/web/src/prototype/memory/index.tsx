// PROTOTYPE, throwaway. Three variants of the memory browser, switchable via `?variant=`,
// inside the sidebar shell with a stub instance. No instance: every call lands in the state panel.
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
import { BotIcon, SparklesIcon } from "lucide-react"

import { PrototypeSwitcher } from "../prototype-switcher"
import { useMemory } from "./flow"
import { StatePanel, Stopped } from "./parts"
import { INSTANCE } from "./stub"
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

export function MemoryPrototype() {
  const [variant, setVariant] = useState(currentVariant)
  const memory = useMemory()
  const { Component } = VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0]

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
              <SidebarGroupLabel>Instances</SidebarGroupLabel>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton isActive>
                    <BotIcon />
                    <span>{INSTANCE.name}</span>
                  </SidebarMenuButton>
                  <SidebarMenuSub>
                    <SidebarMenuSubItem>
                      <SidebarMenuSubButton>Fix flaky hub test</SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                    <SidebarMenuSubItem>
                      <SidebarMenuSubButton>Configuration</SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                    <SidebarMenuSubItem>
                      <SidebarMenuSubButton isActive>Memory</SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  </SidebarMenuSub>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>
        <SidebarInset className="h-svh">
          <header className="flex h-12 shrink-0 items-center gap-2 px-2">
            <SidebarTrigger />
          </header>
          {memory.running ? <Component key={variant} memory={memory} /> : <Stopped />}
        </SidebarInset>
      </SidebarProvider>
      <StatePanel memory={memory} />
      <PrototypeSwitcher variants={VARIANTS} current={variant} onChange={change} />
    </TooltipProvider>
  )
}
