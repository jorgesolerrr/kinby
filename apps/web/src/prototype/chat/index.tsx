// PROTOTYPE, throwaway. Three variants of the chat panel, switchable via `?variant=`, inside the
// real sidebar shell with a stub instance. No hub: calls land in the state panel, and a scripted
// instance answers over the contract's own event types.
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import {
  Sidebar,
  SidebarContent,
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
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { BotIcon, PlusIcon, SparklesIcon } from "lucide-react"

import { PrototypeSwitcher } from "../prototype-switcher"
import { type Chat, threadStatus, threadTitle, useChat } from "./flow"
import { StatePanel } from "./parts"
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

export function ChatPrototype() {
  const [variant, setVariant] = useState(currentVariant)
  const chat = useChat()
  const { Component } = VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0]

  const change = (key: string) => {
    window.history.replaceState(null, "", `?variant=${key}`)
    setVariant(key)
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        <StubSidebar chat={chat} nested={variant === "C"} />
        <SidebarInset className="h-svh">
          <header className="flex h-12 shrink-0 items-center gap-2 px-2">
            <SidebarTrigger />
          </header>
          <Component key={variant} chat={chat} />
        </SidebarInset>
      </SidebarProvider>
      <StatePanel chat={chat} />
      <PrototypeSwitcher variants={VARIANTS} current={variant} onChange={change} />
    </TooltipProvider>
  )
}

const statusLabel = { running: "working", approval: "needs you", failed: "failed", idle: null }

function StubSidebar({ chat, nested }: { chat: Chat; nested: boolean }) {
  const waiting = chat.threads.filter((t) => threadStatus(t) === "approval").length
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
              <SidebarMenuButton isActive>
                <BotIcon />
                <span>{INSTANCE.name}</span>
              </SidebarMenuButton>
              {nested ? (
                <SidebarMenuAction onClick={chat.createThread} aria-label="New thread">
                  <PlusIcon />
                </SidebarMenuAction>
              ) : (
                waiting > 0 && <SidebarMenuBadge>{waiting}</SidebarMenuBadge>
              )}
              {nested && (
                <SidebarMenuSub>
                  {chat.threads.map((t) => {
                    const status = statusLabel[threadStatus(t)]
                    return (
                      <SidebarMenuSubItem key={t.id}>
                        <SidebarMenuSubButton
                          isActive={t.id === chat.thread.id}
                          onClick={() => chat.select(t.id)}
                        >
                          <span className="truncate">{threadTitle(t)}</span>
                          {status && (
                            <Badge variant={status === "failed" ? "destructive" : "secondary"}>
                              {status}
                            </Badge>
                          )}
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    )
                  })}
                </SidebarMenuSub>
              )}
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton>
                <BotIcon />
                <span>Life mate</span>
              </SidebarMenuButton>
              <SidebarMenuBadge>stopped</SidebarMenuBadge>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  )
}
