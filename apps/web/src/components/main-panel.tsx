import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { ConfigPanel } from "@/components/config-panel"
import { InstancePage } from "@/components/instance-page"
import { MemoryPage } from "@/components/memory-page"
import { StatsPage } from "@/components/stats-page"
import { ThreadPanel } from "@/components/thread-panel"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { instanceName } from "@/lib/instances"
import { CirclePauseIcon, MousePointerClickIcon, ServerIcon } from "lucide-react"

/**
 * The selected thread's panel, the selected instance's config panel, memory page, stats page, or
 * page, or why there is none. A thread, the config panel, the memory page, or the stats page opens
 * once `instanceClient` reaches its instance. The config panel opens only on an instance meant to
 * run, which is the one the hub relays to, and stays open while an update restarts the container.
 * The memory and stats pages need a running instance. `onChanged` lists the instances again.
 */
export function MainPanel({
  caller,
  clock = browserClock,
  instances,
  selected,
  threadId,
  configOpen = false,
  memoryOpen = false,
  statsOpen = false,
  instanceClient,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
  instances: InstanceSummary[] | undefined
  selected: InstanceSummary | undefined
  threadId: string | undefined
  configOpen?: boolean
  memoryOpen?: boolean
  statsOpen?: boolean
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
        threadId={threadId}
        name={instanceName(selected)}
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
      return (
        <EmptyState icon={<CirclePauseIcon />} title={`${instanceName(selected)} is not running`}>
          Start it to see its memory.
        </EmptyState>
      )
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
      return (
        <EmptyState icon={<CirclePauseIcon />} title={`${instanceName(selected)} is not running`}>
          Start it to see its stats.
        </EmptyState>
      )
    }
    if (instanceClient === undefined) return null
    return (
      <StatsPage
        key={selected.instance_id}
        client={instanceClient}
        clock={clock}
        instanceId={selected.instance_id}
      />
    )
  }
  if (selected !== undefined) {
    return (
      <InstancePage
        key={selected.instance_id}
        caller={caller}
        clock={clock}
        instance={selected}
        onChanged={onChanged}
      />
    )
  }
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

function EmptyState({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">{icon}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{children}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
