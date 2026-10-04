import type { Client, Clock, InstanceClient, InstanceSummary, ThreadStatus } from "@kinby/contract"
import type * as React from "react"
import { useCallback, useSyncExternalStore } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Badge } from "@/components/ui/badge"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { usePolled } from "@/hooks/use-polled"
import { instanceName, shownState } from "@/lib/instances"
import { CREATE_PATH, instancePath, openCreateWizard, selectInstance } from "@/lib/selection"
import { CircleAlertIcon, PlusIcon } from "lucide-react"

export function NavInstances({
  instances,
  selected,
  creating,
  client,
  clock,
  threads,
}: {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  /** Reaches the instances that are not selected, to count their threads that need the user. */
  client: Pick<Client, "instance">
  clock: Clock
  /** Shown in the selected instance's row, in place of its state. */
  threads: React.ReactNode
}) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        {instances.map((instance) => {
          const name = instanceName(instance)
          const isSelected = instance === selected
          return (
            <SidebarMenuItem key={instance.instance_id}>
              <SidebarMenuButton
                size="lg"
                tooltip={name}
                isActive={isSelected}
                aria-current={isSelected ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault()
                  selectInstance(instance.instance_id)
                }}
                render={
                  <a href={instancePath(instance.instance_id)}>
                    <InstanceAvatar avatar={instance.avatar} name={name} />
                    <span>{name}</span>
                  </a>
                }
              />
              {isSelected && threads ? (
                threads
              ) : !isSelected && instance.process === "running" ? (
                <NeedsYouBadge client={client} clock={clock} instance={instance} />
              ) : (
                <StateBadge instance={instance} />
              )}
            </SidebarMenuItem>
          )
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="New instance"
            isActive={creating}
            aria-current={creating ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openCreateWizard()
            }}
            render={
              <a href={CREATE_PATH}>
                <PlusIcon />
                <span>New instance</span>
              </a>
            }
          />
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}

/** How often an instance that is not selected has its threads counted while the page is visible. */
const COUNT_INTERVAL_MS = 5_000

/** The statuses that wait on the user. */
const NEEDS_YOU: ReadonlySet<ThreadStatus> = new Set(["awaiting_approval", "failed"])

/**
 * How many of a running instance's sidebar threads need the user, in place of its state. It reads
 * the instance on a connection of its own, open while the row shows the count.
 */
function NeedsYouBadge({
  client,
  clock,
  instance,
}: {
  client: Pick<Client, "instance">
  clock: Clock
  instance: InstanceSummary
}) {
  const instanceClient = useInstanceClient(client, instance.instance_id)
  if (instanceClient === undefined) return <StateBadge instance={instance} />
  return <NeedsYouCount client={instanceClient} clock={clock} instance={instance} />
}

function NeedsYouCount({
  client,
  clock,
  instance,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  clock: Clock
  instance: InstanceSummary
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const count = useCallback(
    () =>
      client
        .call("thread.list", { filter: "sidebar" })
        .then(({ threads }) => threads.filter((thread) => NEEDS_YOU.has(thread.status)).length),
    [client],
  )
  const [needsYou = 0] = usePolled(count, clock, connected, COUNT_INTERVAL_MS)
  if (needsYou === 0) return <StateBadge instance={instance} />
  return (
    <SidebarMenuBadge>
      <Badge>
        <span className="sr-only">Threads that need you:</span> {needsYou}
      </Badge>
    </SidebarMenuBadge>
  )
}

function StateBadge({ instance }: { instance: InstanceSummary }) {
  return (
    <SidebarMenuBadge>
      <span className="flex items-center gap-1">
        {instance.setup_pending && (
          <>
            <CircleAlertIcon aria-hidden className="size-3.5" />
            <span className="sr-only">Setup pending</span>
          </>
        )}
        {shownState(instance)}
      </span>
    </SidebarMenuBadge>
  )
}
