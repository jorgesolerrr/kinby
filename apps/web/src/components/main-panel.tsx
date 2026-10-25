import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { ConfigPanel } from "@/components/config-panel"
import { InstancePage } from "@/components/instance-page"
import { MemoryPage } from "@/components/memory-page"
import { OpenHome } from "@/components/open-home"
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
import { instanceName } from "@/lib/instances"
import { selectInstance } from "@/lib/selection"
import { CirclePauseIcon, MousePointerClickIcon, ServerIcon } from "lucide-react"

/**
 * The selected thread's panel, the selected instance's thread list, config panel, memory page,
 * stats page, one of its views, or its page, or why there is none. A thread, the thread list, the
 * config panel, the memory page, the stats page, or a view opens once `instanceClient` reaches its
 * instance. The config panel opens only on an instance meant to run, which is the one the hub
 * relays to, and stays open while an update restarts the container. The thread list, memory and
 * stats pages and a view need a running instance. With none selected, it opens the home instance.
 * `onChanged` lists the instances again.
 */
export function MainPanel({
  caller,
  clock = browserClock,
  instances,
  selected,
  threadId,
  threadsOpen = false,
  configOpen = false,
  memoryOpen = false,
  statsOpen = false,
  viewId,
  instanceClient,
  onChanged,
}: {
  caller: Pick<Client, "call" | "instance">
  clock?: Clock
  instances: InstanceSummary[] | undefined
  selected: InstanceSummary | undefined
  threadId: string | undefined
  threadsOpen?: boolean
  configOpen?: boolean
  memoryOpen?: boolean
  statsOpen?: boolean
  viewId?: string
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
  if (selected !== undefined && configOpen && selected.intended_state === "running") {
    if (instanceClient === undefined) return null
    return (
      <ConfigPanel
        key={selected.instance_id}
        client={instanceClient}
        caller={caller}
        clock={clock}
        instance={selected}
        onChanged={onChanged}
      />
    )
  }
  if (selected !== undefined && memoryOpen) {
    if (selected.process !== "running") {
      return <NotRunning instance={selected} what="memory" />
    }
    if (instanceClient === undefined) return null
    return (
      <MemoryPage
        key={selected.instance_id}
        client={instanceClient}
        clock={clock}
        instanceId={selected.instance_id}
      />
    )
  }
  if (selected !== undefined && statsOpen) {
    if (selected.process !== "running") {
      return <NotRunning instance={selected} what="stats" />
    }
    if (instanceClient === undefined) return null
    return (
      <StatsPage
        key={selected.instance_id}
        client={instanceClient}
        caller={caller}
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
      <ViewPage key={`${selected.instance_id} ${viewId}`} client={instanceClient} viewId={viewId} />
    )
  }
  if (selected !== undefined) {
    return (
      <InstancePage
        key={selected.instance_id}
        caller={caller}
        client={instanceClient}
        clock={clock}
        instance={selected}
        onChanged={onChanged}
      />
    )
  }
  const home = instances.find((instance) => instance.home)
  if (home !== undefined) return <OpenHome client={caller} home={home} />
  if (instances.length === 0) {
    return (
      <EmptyState icon={<ServerIcon />} title="No instances yet">
        The instances this hub manages will show in the sidebar.
      </EmptyState>
    )
  }
  return (
    <EmptyState icon={<MousePointerClickIcon />} title="No instance selected">
      Pick one in the sidebar.
    </EmptyState>
  )
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
