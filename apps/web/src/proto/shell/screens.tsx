// PROTOTYPE: the main-area screens both shell variants open. The thread and composer here are
// stand-ins; their design belongs to the "Thread, composer and message stream" ticket.
import {
  ArrowUpIcon,
  BellDotIcon,
  CircleAlertIcon,
  LoaderCircleIcon,
  type LucideIcon,
  MousePointerClickIcon,
  PlusIcon,
  ShieldAlertIcon,
} from "lucide-react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type * as React from "react"
import { createContext, useContext, useState } from "react"

import {
  HUB_PAGES,
  type HubPage,
  INSTANCES,
  INSTANCE_TABS,
  type InstanceTab,
  type MockInstance,
  REMOVED,
  type Screen,
  type Status,
  instanceById,
} from "./mock"

export type Go = (screen: Screen) => void

/** Lets /proto/instance draw the instance page inside this shell. */
export const InstanceSlot = createContext<
  ((instanceId: string, tab: InstanceTab, go: Go) => React.ReactNode) | undefined
>(undefined)

/** Lets /proto/thread draw the thread and the composer inside this shell. */
export const ThreadSlot = createContext<{
  thread?: (instanceId: string) => React.ReactNode
  composer?: (instanceId: string, pick?: boolean) => React.ReactNode
}>({})

const STATUS: Record<Status, { label: string; icon: LucideIcon; className: string } | undefined> = {
  idle: undefined,
  running: {
    label: "Working",
    icon: LoaderCircleIcon,
    className: "animate-spin text-muted-foreground",
  },
  awaiting_approval: { label: "Needs you", icon: ShieldAlertIcon, className: "text-ember" },
  awaiting_takeover: { label: "Needs you", icon: MousePointerClickIcon, className: "text-ember" },
  awaiting_reply: { label: "Needs your reply", icon: BellDotIcon, className: "text-ember" },
  failed: { label: "Failed", icon: CircleAlertIcon, className: "text-destructive" },
}

export function StatusIcon({ status }: { status: Status }) {
  const shown = STATUS[status]
  if (shown === undefined) return null
  const Icon = shown.icon
  return <Icon aria-label={shown.label} className={shown.className} />
}

/** The composer, with the instance a new thread goes to picked inside it. */
function Composer({ instanceId, pick }: { instanceId: string; pick?: boolean }) {
  const [to, setTo] = useState(instanceId)
  const instance = instanceById(to)
  return (
    <InputGroup className="h-auto">
      <InputGroupTextarea placeholder={`Message ${instance.name}…`} className="min-h-16" />
      <InputGroupAddon align="block-end">
        <InputGroupButton size="icon-xs" aria-label="Add">
          <PlusIcon />
        </InputGroupButton>
        {pick && (
          <DropdownMenu>
            <DropdownMenuTrigger render={<InputGroupButton />}>
              <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
              {instance.name}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuGroup>
                <DropdownMenuLabel>Send to</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={to} onValueChange={(v: string) => setTo(v)}>
                  {INSTANCES.filter((i) => i.state === "running").map((i) => (
                    <DropdownMenuRadioItem key={i.id} value={i.id}>
                      {i.name}
                      {i.home && " (home)"}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        <InputGroupButton variant="default" size="icon-xs" className="ml-auto" aria-label="Send">
          <ArrowUpIcon />
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  )
}

export function HomeScreen({ instanceId, pick }: { instanceId: string; pick?: boolean }) {
  const instance = instanceById(instanceId)
  const slot = useContext(ThreadSlot)
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4 pb-24">
      <h1 className="font-heading text-3xl font-semibold">Good evening, Jorge</h1>
      <div className="w-full max-w-3xl">
        {slot.composer?.(instance.id, pick) ?? (
          <Composer key={instance.id} instanceId={instance.id} pick={pick} />
        )}
      </div>
    </div>
  )
}

export function ThreadScreen({ instanceId, threadId }: { instanceId: string; threadId: string }) {
  const instance = instanceById(instanceId)
  const thread = instance.threads.find((t) => t.id === threadId) ?? instance.threads[0]
  const slot = useContext(ThreadSlot)
  if (slot.thread) return slot.thread(instanceId)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8">
          <Bubble variant="secondary" align="end">
            <BubbleContent>
              {thread.title}. Keep it under 900 euros and avoid early flights.
            </BubbleContent>
          </Bubble>
          <div className="font-reading text-base leading-7">
            <p>
              I found three options for the week of 9 March. The cheapest keeps you under budget
              with a 10:40 flight out on Monday and back on Friday evening.
            </p>
          </div>
          <Badge variant="outline" className="self-start">
            Searched 3 sites · read 2 pages
          </Badge>
          <div className="font-reading text-base leading-7">
            <p>
              Want me to hold the Alfama apartment while you decide? It's refundable until Thursday.
            </p>
          </div>
        </div>
      </div>
      <div className="mx-auto w-full max-w-3xl px-4 pb-4">
        <Composer instanceId={instance.id} />
      </div>
    </div>
  )
}

export function ThreadsScreen({ instanceId, go }: { instanceId: string; go: Go }) {
  const instance = instanceById(instanceId)
  return (
    <Page title={`${instance.name} · all threads`}>
      <ItemGroup>
        {instance.threads.map((t) => (
          <Item
            key={t.id}
            variant="outline"
            render={
              <button
                type="button"
                onClick={() => go({ kind: "thread", instanceId, threadId: t.id })}
              />
            }
          >
            <ItemContent>
              <ItemTitle>{t.title}</ItemTitle>
              <ItemDescription>
                {t.day}
                {t.routine && " · routine"}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <StatusIcon status={t.status} />
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
    </Page>
  )
}

/** A page with a title, centred like settings pages in the reference apps. */
export function Page({
  title,
  actions,
  children,
}: {
  title: React.ReactNode
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-8 md:px-8">
        <div className="flex items-center gap-3">
          <h1 className="font-heading text-xl font-semibold">{title}</h1>
          <div className="ml-auto flex gap-2">{actions}</div>
        </div>
        {children}
      </div>
    </div>
  )
}

/**
 * An instance's own surfaces. `tabs` shows them as tabs on one page (variant A); without it, the
 * shell's own nav picks the tab and the page shows just that surface (variant B).
 */
export function InstanceScreen({
  instanceId,
  tab,
  tabs,
  go,
}: {
  instanceId: string
  tab: InstanceTab
  tabs?: boolean
  go: Go
}) {
  const instance = instanceById(instanceId)
  const title = (
    <span className="flex items-center gap-3">
      <InstanceAvatar avatar={instance.avatar} name={instance.name} />
      {instance.name}
      {!tabs && tab !== "overview" && (
        <span className="text-muted-foreground">
          · {INSTANCE_TABS.find((t) => t.id === tab)?.label}
        </span>
      )}
    </span>
  )
  const actions = (
    <>
      <Badge variant={instance.state === "running" ? "secondary" : "outline"}>
        {instance.state}
      </Badge>
      <Button variant="outline" size="sm">
        {instance.state === "running" ? "Stop" : "Start"}
      </Button>
    </>
  )
  if (!tabs) {
    return (
      <Page title={title} actions={actions}>
        <InstanceSurface instance={instance} tab={tab} />
      </Page>
    )
  }
  return (
    <Page title={title} actions={actions}>
      <Tabs
        value={tab}
        onValueChange={(v: InstanceTab) => go({ kind: "instance", instanceId, tab: v })}
      >
        <TabsList>
          {INSTANCE_TABS.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {INSTANCE_TABS.map((t) => (
          <TabsContent key={t.id} value={t.id} className="pt-4">
            <InstanceSurface instance={instance} tab={t.id} />
          </TabsContent>
        ))}
      </Tabs>
    </Page>
  )
}

function InstanceSurface({ instance, tab }: { instance: MockInstance; tab: InstanceTab }) {
  switch (tab) {
    case "overview":
      return (
        <div className="grid gap-4 md:grid-cols-2">
          <Stub title="Browser profiles" body="Personal (signed in to 4 sites), Work" />
          <Stub title="Vault" body="12 items" />
          <Stub
            title="Persona"
            body={instance.role ?? (instance.home ? "Home instance" : "Research helper")}
          />
          <Stub title="Image" body="kinby 0.42.1 · up to date" />
        </div>
      )
    case "config":
      return (
        <div className="grid gap-4">
          <Stub title="Model" body="claude-opus-5-5 · effort high" />
          <Stub title="Tools and permissions" body="Browser, shell (ask), files" />
          <Stub title="Secrets" body="3 instance secrets, 2 hub secrets" />
        </div>
      )
    case "routines":
      return (
        <div className="grid gap-4">
          <Stub title="Morning brief" body="Weekdays at 07:30" />
          <Stub title="Weekly finance check" body="Sundays at 18:00 · last run failed" />
        </div>
      )
    case "memory":
      return <Stub title="Long-term memory" body="214 facts · last dreaming 03:10 today" />
    case "stats":
      return <Stub title="Last 30 days" body="182 runs · $14.20 · 96% succeeded" />
  }
}

function Stub({ title, body }: { title: string; body: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{body}</CardDescription>
      </CardHeader>
    </Card>
  )
}

export function ViewScreen({ instanceId, viewId }: { instanceId: string; viewId: string }) {
  const instance = instanceById(instanceId)
  const view = instance.views.find((v) => v.id === viewId)
  return (
    <Page title={view?.title ?? "View"}>
      <Empty className="border">
        <EmptyHeader>
          <EmptyTitle>{view?.title}</EmptyTitle>
          <EmptyDescription>A view {instance.name} published renders here.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    </Page>
  )
}

export function LibraryScreen() {
  return (
    <Page title="Library" actions={<Button size="sm">New skill</Button>}>
      <Tabs defaultValue="skills">
        <TabsList>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="mcp">MCP servers</TabsTrigger>
          <TabsTrigger value="factories">Factories</TabsTrigger>
        </TabsList>
        <TabsContent value="skills" className="pt-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Stub title="implement-ticket" body="Used by Coder" />
            <Stub title="repository-survey" body="Used by Coder" />
            <Stub title="trip-planner" body="Used by Kin" />
            <Stub title="paper-digest" body="Used by Scout" />
          </div>
        </TabsContent>
        <TabsContent value="mcp" className="pt-4">
          <Stub title="github" body="2 instances" />
        </TabsContent>
        <TabsContent value="factories" className="pt-4">
          <Stub title="software" body="1 factory instance: Coder" />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

export function NewInstanceScreen() {
  return (
    <Page title="New instance">
      <Empty className="border">
        <EmptyHeader>
          <EmptyTitle>Onboarding</EmptyTitle>
          <EmptyDescription>
            The new-instance flow lands here (onboarding is its own ticket).
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    </Page>
  )
}

/** The main area for a screen. `instanceTabs` puts an instance's surfaces in tabs on its page. */
export function MainScreen({
  screen,
  go,
  instanceTabs,
  homeId,
  pick,
}: {
  screen: Screen
  go: Go
  instanceTabs?: boolean
  /** The instance a new thread goes to from the home screen. */
  homeId: string
  pick?: boolean
}) {
  const instancePage = useContext(InstanceSlot)
  if (screen.kind === "instance" && instancePage !== undefined) {
    return instancePage(screen.instanceId, screen.tab, go)
  }
  switch (screen.kind) {
    case "home":
      return <HomeScreen instanceId={homeId} pick={pick} />
    case "thread":
      return (
        <ThreadScreen
          key={screen.threadId}
          instanceId={screen.instanceId}
          threadId={screen.threadId}
        />
      )
    case "threads":
      return <ThreadsScreen instanceId={screen.instanceId} go={go} />
    case "instance":
      return (
        <InstanceScreen
          instanceId={screen.instanceId}
          tab={screen.tab}
          tabs={instanceTabs}
          go={go}
        />
      )
    case "view":
      return <ViewScreen instanceId={screen.instanceId} viewId={screen.viewId} />
    case "library":
      return <LibraryScreen />
    case "new-instance":
      return <NewInstanceScreen />
    case "hub":
      return (
        <Page title={HUB_PAGES.find((p) => p.id === screen.page)?.label}>
          <HubContent page={screen.page} />
        </Page>
      )
  }
}

/** A hub page's body: the same in A's Settings dialog and on B's full pages. */
export function HubContent({ page }: { page: HubPage }) {
  switch (page) {
    case "general":
      return (
        <ItemGroup>
          <Item variant="outline">
            <ItemContent>
              <ItemTitle>Theme</ItemTitle>
              <ItemDescription>Follows the system</ItemDescription>
            </ItemContent>
          </Item>
          <Item variant="outline">
            <ItemContent>
              <ItemTitle>Send notifications</ItemTitle>
              <ItemDescription>When a thread needs you</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Switch defaultChecked />
            </ItemActions>
          </Item>
        </ItemGroup>
      )
    case "usage":
      return (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Instance</TableHead>
              <TableHead className="text-right">Runs</TableHead>
              <TableHead className="text-right">Spend</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {INSTANCES.map((i, n) => (
              <TableRow key={i.id}>
                <TableCell>{i.name}</TableCell>
                <TableCell className="text-right tabular-nums">{[182, 64, 41, 3][n]}</TableCell>
                <TableCell className="text-right tabular-nums">
                  ${[14.2, 9.8, 22.4, 0.3][n]}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )
    case "secrets":
      return (
        <ItemGroup>
          {["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "BRAVE_API_KEY"].map((name) => (
            <Item key={name} variant="outline">
              <ItemContent>
                <ItemTitle className="font-mono">{name}</ItemTitle>
                <ItemDescription>Shared with 2 instances</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button variant="ghost" size="sm">
                  Edit
                </Button>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      )
    case "channels":
      return (
        <ItemGroup>
          <Item variant="outline">
            <ItemContent>
              <ItemTitle>Telegram</ItemTitle>
              <ItemDescription>@jorge · notifications and replies</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Switch defaultChecked />
            </ItemActions>
          </Item>
          <Item variant="outline">
            <ItemContent>
              <ItemTitle>Email</ItemTitle>
              <ItemDescription>Not connected</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button variant="outline" size="sm">
                Connect
              </Button>
            </ItemActions>
          </Item>
        </ItemGroup>
      )
    case "instances":
      return (
        <div className="flex flex-col gap-4">
          <ItemGroup>
            {INSTANCES.map((i) => (
              <Item key={i.id} variant="outline">
                <ItemMedia>
                  <InstanceAvatar avatar={i.avatar} name={i.name} />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>
                    {i.name}
                    {i.home && <Badge variant="secondary">Home</Badge>}
                  </ItemTitle>
                  <ItemDescription>{i.state}</ItemDescription>
                </ItemContent>
              </Item>
            ))}
          </ItemGroup>
          <Separator />
          <h2 className="font-heading text-base font-semibold">Removed</h2>
          <ItemGroup>
            {REMOVED.map((i) => (
              <Item key={i.id} variant="outline">
                <ItemMedia>
                  <InstanceAvatar avatar={i.avatar} name={i.name} />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>{i.name}</ItemTitle>
                  <ItemDescription>Removed 3 days ago</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Button variant="outline" size="sm">
                    Restore
                  </Button>
                </ItemActions>
              </Item>
            ))}
          </ItemGroup>
        </div>
      )
  }
}
