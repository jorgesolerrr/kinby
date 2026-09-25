import type { InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { instanceName } from "@/lib/instances"
import { BotIcon, MousePointerClickIcon, ServerIcon } from "lucide-react"

/** What the selected instance shows. No flow fills it yet, so every case is an empty state. */
export function MainPanel({
  instances,
  selected,
}: {
  instances: InstanceSummary[] | undefined
  selected: InstanceSummary | undefined
}) {
  if (instances === undefined) return null
  if (selected !== undefined) {
    return (
      <EmptyState icon={<BotIcon />} title="Nothing here yet">
        What {instanceName(selected)} does will show here.
      </EmptyState>
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
