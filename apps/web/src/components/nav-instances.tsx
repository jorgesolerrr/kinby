import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import { useCallback, useId, useSyncExternalStore } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { usePolled } from "@/hooks/use-polled"
import { instanceName, shownState } from "@/lib/instances"
import {
  CREATE_PATH,
  instancePath,
  openOnboarding,
  openRemoved,
  REMOVED_PATH,
  selectInstance,
} from "@/lib/selection"
import { needsYou, threadList } from "@/lib/thread-list"
import { ArchiveRestoreIcon, PlusIcon } from "lucide-react"

export function NavInstances({
  instances,
  selected,
  creating,
  anyRemoved,
  removedOpen,
  client,
  clock,
  selectedClient,
}: {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  /** Whether the hub has a removed instance, to link to the Removed instances page at the foot. */
  anyRemoved: boolean
  removedOpen: boolean
  /** Reaches the instances that are not selected, to count their threads that need the user. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's connection, whose threads column lists the threads its row counts. */
  selectedClient: Pick<InstanceClient, "call"> | undefined
}) {
  const home = instances.filter((instance) => instance.home)
  const others = instances.filter((instance) => !instance.home)
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        {[...home, ...others].map((instance) => {
          const name = instanceName(instance)
          const isSelected = instance === selected
          return (
            <InstanceRow
              key={instance.instance_id}
              instance={instance}
              name={name}
              isSelected={isSelected}
              client={client}
              clock={clock}
              selectedClient={isSelected ? selectedClient : undefined}
            />
          )
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="New instance"
            isActive={creating}
            aria-current={creating ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openOnboarding()
            }}
            render={
              <a href={CREATE_PATH}>
                <PlusIcon />
                <span>New instance</span>
              </a>
            }
          />
        </SidebarMenuItem>
        {anyRemoved && (
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip="Removed instances"
              isActive={removedOpen}
              aria-current={removedOpen ? "page" : undefined}
              onClick={(event) => {
                event.preventDefault()
                openRemoved()
              }}
              render={
                <a href={REMOVED_PATH}>
                  <ArchiveRestoreIcon />
                  <span>Removed instances</span>
                </a>
              }
            />
          </SidebarMenuItem>
        )}
      </SidebarMenu>
    </SidebarGroup>
  )
}

/**
 * An instance's row: its avatar, its name, and under the name what needs the user or else its
 * state, with an ember dot while something waits. The link is named by the instance alone; the
 * second line is its description. The selected instance counts the threads its threads column
 * lists, and the others count on connections of their own.
 */
function InstanceRow({
  instance,
  name,
  isSelected,
  client,
  clock,
  selectedClient,
}: {
  instance: InstanceSummary
  name: string
  isSelected: boolean
  client: Pick<Client, "instance">
  clock: Clock
  selectedClient: Pick<InstanceClient, "call"> | undefined
}) {
  const statusId = useId()
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        size="lg"
        tooltip={name}
        isActive={isSelected}
        aria-current={isSelected ? "page" : undefined}
        aria-describedby={statusId}
        onClick={(event) => {
          event.preventDefault()
          selectInstance(instance.instance_id)
        }}
        render={
          <a href={instancePath(instance.instance_id)}>
            <InstanceAvatar avatar={instance.avatar} name={name} />
            {selectedClient !== undefined ? (
              <ListedNeedsYou client={selectedClient} instance={instance} id={statusId} />
            ) : !isSelected && instance.process === "running" ? (
              <NeedsYouBadge client={client} clock={clock} instance={instance} id={statusId} />
            ) : (
              <NeedsYou count={instance.needs_human} instance={instance} id={statusId} />
            )}
          </a>
        }
      />
    </SidebarMenuItem>
  )
}

/** How often an instance that is not selected has its threads counted while the page is visible. */
const COUNT_INTERVAL_MS = 5_000

/**
 * How many of a running instance's sidebar threads and factory runs need the user, in place of its
 * state. It reads the threads on a connection of its own, open while the row shows the count, and
 * the runs from the hub's summary of the instance.
 */
function NeedsYouBadge({
  client,
  clock,
  instance,
  id,
}: {
  client: Pick<Client, "instance">
  clock: Clock
  instance: InstanceSummary
  id: string
}) {
  const instanceClient = useInstanceClient(client, instance.instance_id)
  if (instanceClient === undefined) {
    return <NeedsYou count={instance.needs_human} instance={instance} id={id} />
  }
  return <NeedsYouCount client={instanceClient} clock={clock} instance={instance} id={id} />
}

function NeedsYouCount({
  client,
  clock,
  instance,
  id,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  clock: Clock
  instance: InstanceSummary
  id: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const count = useCallback(
    () =>
      client
        .call("thread.list", { filter: "sidebar" })
        .then(({ threads }) => threads.filter(needsYou).length),
    [client],
  )
  const [threadsNeedingYou = 0] = usePolled(count, clock, connected, COUNT_INTERVAL_MS)
  return <NeedsYou count={threadsNeedingYou + instance.needs_human} instance={instance} id={id} />
}

/** What needs the user among the threads the selected instance's threads column last listed. */
function ListedNeedsYou({
  client,
  instance,
  id,
}: {
  client: Pick<InstanceClient, "call">
  instance: InstanceSummary
  id: string
}) {
  const list = threadList(client, "sidebar")
  const threads = useSyncExternalStore(list.onChange, list.view)?.threads ?? []
  return (
    <NeedsYou
      count={threads.filter(needsYou).length + instance.needs_human}
      instance={instance}
      id={id}
    />
  )
}

/**
 * The name over what needs the user, or over the instance's state when nothing does. A row that
 * cannot count threads still counts the factory runs from the hub's summary. The second line is
 * hidden from the link's name and read as its description, through `id`.
 */
function NeedsYou({
  count,
  instance,
  id,
}: {
  count: number
  instance: InstanceSummary
  id: string
}) {
  return (
    <>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium">{instanceName(instance)}</span>
        <span id={id} aria-hidden className="truncate font-mono text-xs text-muted-foreground">
          {count > 0 ? (
            `${count} waiting for you`
          ) : (
            <>
              {instance.setup_pending && (
                <>
                  <span>Setup pending</span> ·{" "}
                </>
              )}
              <span>{shownState(instance)}</span>
            </>
          )}
        </span>
      </span>
      {count > 0 && <span aria-hidden className="size-2 shrink-0 rounded-full bg-ember" />}
    </>
  )
}
