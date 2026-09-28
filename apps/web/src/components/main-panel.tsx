import { browserClock } from "@kinby/contract"
import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { ConfigPanel } from "@/components/config-panel"
import { InstancePage } from "@/components/instance-page"
import { ThreadPanel } from "@/components/thread-panel"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { instanceName } from "@/lib/instances"
import { MousePointerClickIcon, ServerIcon } from "lucide-react"

/**
 * The selected thread's panel, the selected instance's config panel or page, or why there is none.
 * A thread or the config panel opens once `instanceClient` reaches its instance, and the config
 * panel only on a running instance, which is the one the hub relays to. `onChanged` lists the
 * instances again.
 */
export function MainPanel({
  caller,
  clock = browserClock,
  instances,
  selected,
  threadId,
  configOpen = false,
  instanceClient,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
  instances: InstanceSummary[] | undefined
  selected: InstanceSummary | undefined
  threadId: string | undefined
  configOpen?: boolean
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
  if (selected !== undefined && configOpen && selected.process === "running") {
    if (instanceClient === undefined) return null
    return <ConfigPanel key={selected.instance_id} client={instanceClient} />
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
