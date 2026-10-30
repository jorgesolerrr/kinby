import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { ConfigPanel } from "@/components/config-panel"
import { InstancePage } from "@/components/instance-page"
import { MemoryPage } from "@/components/memory-page"
import { HomeScreen } from "@/components/home-screen"
import { RoutinesSection } from "@/components/routines-section"
import { StatsPage } from "@/components/stats-page"
import { ThreadListPage } from "@/components/thread-list-page"
import { ThreadPanel } from "@/components/thread-panel"
import { ViewPage } from "@/components/view-page"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { INSTANCE_TABS, instanceName } from "@/lib/instances"
import { behind, OlderCore } from "@/lib/older-core"
import {
  type InstanceTab,
  linkedRoutine,
  openCore,
  openOnboarding,
  openTab,
  selectInstance,
} from "@/lib/selection"
import { CirclePauseIcon, ServerIcon } from "lucide-react"

/**
 * The selected thread's panel, the selected instance's thread list, one of its views, or its page
 * open on `tab`, or why there is none. A thread, the thread list, or a view opens once
 * `instanceClient` reaches its instance, and needs a running one. With none selected, it shows the
 * home screen, preset to send `to` an instance. `onChanged` lists the instances again.
 */
export function MainPanel({
  caller,
  clock = browserClock,
  instances,
  selected,
  threadId,
  threadsOpen = false,
  tab = "overview",
  viewId,
  to,
  instanceClient,
  onChanged,
}: {
  caller: Pick<Client, "call" | "subscribe" | "instance">
  clock?: Clock
  instances: InstanceSummary[] | undefined
  selected: InstanceSummary | undefined
  threadId: string | undefined
  threadsOpen?: boolean
  tab?: InstanceTab
  viewId?: string
  to?: string
  instanceClient: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange"> | undefined
  onChanged: () => void
}) {
  if (instances === undefined) return null
  if (selected !== undefined && threadId !== undefined) {
    if (instanceClient === undefined) return null
    return (
      <ThreadPanel
        key={threadId}
        client={instanceClient}
        hub={caller}
        clock={clock}
        instanceId={selected.instance_id}
        threadId={threadId}
        name={instanceName(selected)}
        avatar={selected.avatar}
      />
    )
  }
  if (selected !== undefined && threadsOpen) {
    if (selected.process !== "running") {
      return <NotRunning instance={selected} what="threads" />
    }
    if (instanceClient === undefined) return null
    return (
      <ThreadListPage
        key={selected.instance_id}
        client={instanceClient}
        clock={clock}
        instanceId={selected.instance_id}
      />
    )
  }
  if (selected !== undefined && viewId !== undefined) {
    if (selected.process !== "running") {
      return <NotRunning instance={selected} what="views" />
    }
    if (instanceClient === undefined) return null
    return (
      <ViewPage
        key={`${selected.instance_id} ${viewId}`}
        client={instanceClient}
        instanceId={selected.instance_id}
        viewId={viewId}
      />
    )
  }
  if (selected !== undefined) {
    return (
      <InstanceTabs instance={selected} tab={tab}>
        <TabBody
          caller={caller}
          clock={clock}
          instance={selected}
          tab={tab}
          instanceClient={instanceClient}
          onChanged={onChanged}
        />
      </InstanceTabs>
    )
  }
  if (instances.length === 0) {
    return (
      <EmptyState
        icon={<ServerIcon />}
        title="No instances yet"
        action={<Button onClick={openOnboarding}>New instance</Button>}
      >
        Create one to start a thread with it.
      </EmptyState>
    )
  }
  return <HomeScreen key={to} caller={caller} instances={instances} to={to} />
}

/** The instance's tab strip, named for the instance, over the open tab. */
function InstanceTabs({
  instance,
  tab,
  children,
}: {
  instance: InstanceSummary
  tab: InstanceTab
  children: React.ReactNode
}) {
  return (
    <Tabs
      value={tab}
      onValueChange={(picked: InstanceTab) => openTab(instance.instance_id, picked)}
      className="min-h-0 flex-1"
    >
      <div className="shrink-0 border-b px-4">
        <TabsList
          variant="line"
          aria-label={instanceName(instance)}
          className="w-full justify-start overflow-x-auto"
        >
          {INSTANCE_TABS.map(({ id, label }) => (
            <TabsTrigger key={id} value={id} className="flex-none">
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      <TabsContent value={tab} className="flex min-h-0 min-w-0 flex-col">
        {/* The tab panel sets small text, and the pages keep the base size they had before. */}
        <div className="flex min-h-0 flex-1 flex-col text-base">{children}</div>
      </TabsContent>
    </Tabs>
  )
}

/**
 * What the open tab holds. Config, Routines, Memory and Stats open once `instanceClient` reaches the
 * instance. Config opens only on an instance meant to run, which is the one the hub relays to, and
 * stays open while an update restarts the container. Routines, Memory and Stats need a running one.
 */
function TabBody({
  caller,
  clock,
  instance,
  tab,
  instanceClient,
  onChanged,
}: {
  caller: Pick<Client, "call" | "subscribe" | "instance">
  clock: Clock
  instance: InstanceSummary
  tab: InstanceTab
  instanceClient: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange"> | undefined
  onChanged: () => void
}) {
  const instanceId = instance.instance_id
  if (tab === "overview") {
    return (
      <InstancePage
        key={instanceId}
        caller={caller}
        client={instanceClient}
        clock={clock}
        instance={instance}
        onChanged={onChanged}
      />
    )
  }
  const state = tab === "config" ? instance.intended_state : instance.process
  if (state !== "running") return <NotRunning instance={instance} what={tab} />
  if (instanceClient === undefined) return null
  switch (tab) {
    case "config":
      return (
        <ConfigPanel
          key={instanceId}
          client={instanceClient}
          caller={caller}
          clock={clock}
          instance={instance}
          onChanged={onChanged}
        />
      )
    case "routines":
      return (
        <div className="flex flex-col gap-4 p-6">
          <div>
            <h1 className="text-xl font-semibold">Routines</h1>
            <p className="text-sm text-muted-foreground">schedules, signals, next firing</p>
          </div>
          <Separator />
          <OlderCore value={behind(instance) ? () => openCore(instanceId) : undefined}>
            <RoutinesSection
              key={instanceId}
              client={instanceClient}
              clock={clock}
              opened={linkedRoutine()}
            />
          </OlderCore>
        </div>
      )
    case "memory":
      return (
        <MemoryPage
          key={instanceId}
          client={instanceClient}
          clock={clock}
          instanceId={instanceId}
        />
      )
    case "stats":
      return (
        <StatsPage
          key={instanceId}
          client={instanceClient}
          caller={caller}
          clock={clock}
          instanceId={instanceId}
        />
      )
  }
}

/** A page that needs a running instance, sending the user to the instance's page, which has Start. */
function NotRunning({ instance, what }: { instance: InstanceSummary; what: string }) {
  const name = instanceName(instance)
  return (
    <EmptyState
      icon={<CirclePauseIcon />}
      title={`${name} is not running`}
      action={<Button onClick={() => selectInstance(instance.instance_id)}>Open {name}</Button>}
    >
      Start it from its page to see its {what}.
    </EmptyState>
  )
}

function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">{icon}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{children}</EmptyDescription>
      </EmptyHeader>
      {action !== undefined && <EmptyContent>{action}</EmptyContent>}
    </Empty>
  )
}
