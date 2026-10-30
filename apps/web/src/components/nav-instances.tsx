import type { Client, Clock, InstanceClient, InstanceSummary, ThreadSummary } from "@kinby/contract"
import { cn } from "cn"
import {
  BellDotIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  LoaderCircleIcon,
  type LucideIcon,
  MoreHorizontalIcon,
  MousePointerClickIcon,
  PlusIcon,
  ShieldAlertIcon,
  SquarePenIcon,
} from "lucide-react"
import { useCallback, useEffect, useId, useState, useSyncExternalStore } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { SearchPalette } from "@/components/search-palette"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { usePolled } from "@/hooks/use-polled"
import { INSTANCE_TABS, instanceName, shownState } from "@/lib/instances"
import {
  CREATE_PATH,
  homePath,
  tabPath,
  threadPath,
  threadsPath,
  useInstanceTab,
  useSelectedThreadId,
  useThreadsOpen,
  useViewOpen,
  viewPath,
} from "@/lib/selection"
import { needsYou, threadList, threadTitle } from "@/lib/thread-list"

type Reached = Pick<InstanceClient, "call" | "state" | "onStateChange">

/** How many of an instance's most recent threads its group lists. */
const RECENT = 4

/** How often a group reads again what it shows of an instance while the page is visible. */
const POLL_INTERVAL_MS = 5_000

/** What a thread's status shows. An idle thread needs no look, so it shows nothing. */
const STATUS: Record<
  ThreadSummary["status"],
  { label: string; icon: LucideIcon; className: string } | undefined
> = {
  idle: undefined,
  running: {
    label: "Working",
    icon: LoaderCircleIcon,
    className: "animate-spin text-muted-foreground",
  },
  awaiting_approval: { label: "Needs you", icon: ShieldAlertIcon, className: "text-foreground" },
  awaiting_takeover: {
    label: "Needs you",
    icon: MousePointerClickIcon,
    className: "text-foreground",
  },
  awaiting_reply: { label: "Needs your reply", icon: BellDotIcon, className: "text-foreground" },
  failed: { label: "Failed", icon: CircleAlertIcon, className: "text-destructive" },
}

/** The groups the user folded or unfolded, open or not, by instance. */
export type Folds = Readonly<Record<string, boolean>>

export function NavInstances({
  instances,
  selected,
  creating,
  client,
  clock,
  selectedClient,
  folds,
  onFold,
  searching,
  onSearchingChange,
}: {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  /** Reaches the instances that are not selected, to list their threads and views. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's connection, whose thread list the open thread reads and changes. */
  selectedClient: Reached | undefined
  /** Kept above the phone's drawer, which unmounts the groups each time it closes. */
  folds: Folds
  onFold: (instanceId: string, open: boolean) => void
  /** Whether the search palette is open. It searches the threads the groups list. */
  searching: boolean
  onSearchingChange: (searching: boolean) => void
}) {
  const open = useOpenFromSidebar()
  const home = instances.filter((instance) => instance.home)
  const others = instances.filter((instance) => !instance.home)
  const ordered = [...home, ...others]
  // Each running instance's group says which connection it lists the instance's threads on.
  const [reached, setReached] = useState<ReadonlyMap<string, Reached>>(new Map())
  const reach = useCallback((instanceId: string, client: Reached | undefined) => {
    setReached((was) => {
      const next = new Map(was)
      if (client === undefined) next.delete(instanceId)
      else next.set(instanceId, client)
      return next
    })
  }, [])
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        {ordered.map((instance) => {
          const isSelected = instance === selected
          return (
            <InstanceGroup
              key={instance.instance_id}
              instance={instance}
              isSelected={isSelected}
              client={client}
              clock={clock}
              selectedClient={isSelected ? selectedClient : undefined}
              folded={folds[instance.instance_id]}
              onFold={(open) => onFold(instance.instance_id, open)}
              onReach={reach}
            />
          )
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={creating}
            aria-current={creating ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              open(CREATE_PATH)
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
      <SearchPalette
        open={searching}
        onOpenChange={onSearchingChange}
        instances={ordered.flatMap((instance) => {
          const client = reached.get(instance.instance_id)
          return client === undefined ? [] : [{ instance, client }]
        })}
      />
    </SidebarGroup>
  )
}

/**
 * An instance's row, which folds the group under it, and its menu. The row says when the instance
 * is not running, and shows an ember dot while its sidebar threads or factory runs need the user.
 * Under a running instance come its views, its most recent sidebar threads, and "All threads". The
 * selected instance lists on the connection the open thread uses, and the others on their own, and
 * `onReach` hears which one it is.
 */
function InstanceGroup({
  instance,
  isSelected,
  client,
  clock,
  selectedClient,
  folded,
  onFold,
  onReach,
}: {
  instance: InstanceSummary
  isSelected: boolean
  client: Pick<Client, "instance">
  clock: Clock
  selectedClient: Reached | undefined
  folded: boolean | undefined
  onFold: (open: boolean) => void
  onReach: (instanceId: string, client: Reached | undefined) => void
}) {
  const name = instanceName(instance)
  const running = instance.process === "running"
  const ownClient = useInstanceClient(
    client,
    !isSelected && running ? instance.instance_id : undefined,
  )
  const reached = running ? (selectedClient ?? ownClient) : undefined
  useEffect(() => {
    onReach(instance.instance_id, reached)
    return () => onReach(instance.instance_id, undefined)
  }, [onReach, instance.instance_id, reached])
  const threads = useSidebarThreads(reached, clock)
  const count = threads.filter(needsYou).length + instance.needs_human
  // A group opens by itself until the user folds or unfolds it, and the user's choice then holds.
  const [opened, setOpened] = useState(false)
  if (!opened && (instance.home || count > 0 || isSelected)) setOpened(true)
  const open = folded ?? opened
  const tab = useInstanceTab()
  return (
    <Collapsible open={open} onOpenChange={onFold} render={<SidebarMenuItem />}>
      <CollapsibleTrigger render={<SidebarMenuButton isActive={isSelected && tab !== undefined} />}>
        <InstanceAvatar avatar={instance.avatar} name={name} size="sm" />
        <span className="truncate font-medium">{name}</span>
        {!running && (
          <span className="shrink-0 text-xs text-muted-foreground">{shownState(instance)}</span>
        )}
        {count > 0 && (
          <span className="ml-auto size-2 shrink-0 rounded-full bg-ember">
            <span className="sr-only">{count} waiting for you</span>
          </span>
        )}
        <ChevronRightIcon
          className={cn("transition-transform", count === 0 && "ml-auto", open && "rotate-90")}
        />
      </CollapsibleTrigger>
      <InstanceMenu instanceId={instance.instance_id} name={name} running={running} />
      {reached !== undefined && (
        <CollapsibleContent>
          <SidebarMenuSub>
            <InstanceViews
              client={reached}
              clock={clock}
              instanceId={instance.instance_id}
              isSelected={isSelected}
            />
            <RecentThreads
              threads={threads.slice(0, RECENT)}
              instanceId={instance.instance_id}
              isSelected={isSelected}
            />
          </SidebarMenuSub>
        </CollapsibleContent>
      )}
    </Collapsible>
  )
}

/**
 * The sidebar set of `client`'s instance, listed again as `usePolled` says. It is the list the open
 * thread reads and changes, so a change there shows here at once. None while nothing reaches it.
 */
function useSidebarThreads(client: Reached | undefined, clock: Clock): ThreadSummary[] {
  const list = client === undefined ? undefined : threadList(client, "sidebar")
  const connected =
    useSyncExternalStore(client?.onStateChange ?? unsubscribed, () => client?.state()) ===
    "connected"
  usePolled(list?.list ?? listNothing, clock, connected, POLL_INTERVAL_MS)
  return useSyncExternalStore(list?.onChange ?? unsubscribed, () => list?.view())?.threads ?? []
}

function unsubscribed(): () => void {
  return () => {}
}

async function listNothing(): Promise<void> {}

/**
 * The row's "…" menu: a new thread with the instance, which only a running one can take, then each
 * of its page's tabs.
 */
function InstanceMenu({
  instanceId,
  name,
  running,
}: {
  instanceId: string
  name: string
  running: boolean
}) {
  const { isMobile } = useSidebar()
  const open = useOpenFromSidebar()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<SidebarMenuAction showOnHover aria-label={`Menu for ${name}`} />}
      >
        <MoreHorizontalIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent side={isMobile ? "bottom" : "right"} align="start">
        <DropdownMenuGroup>
          <DropdownMenuItem disabled={!running} onClick={() => open(homePath(instanceId))}>
            <SquarePenIcon />
            New thread with {name}
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          {INSTANCE_TABS.map(({ id, label }) => (
            <DropdownMenuItem key={id} onClick={() => open(tabPath(instanceId, id))}>
              {label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** An instance's views, each linking to its page, with the one the URL opens marked current. */
function InstanceViews({
  client,
  clock,
  instanceId,
  isSelected,
}: {
  client: Reached
  clock: Clock
  instanceId: string
  isSelected: boolean
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const list = useCallback(() => client.call("view.list", {}).then(({ views }) => views), [client])
  const [views = []] = usePolled(list, clock, connected, POLL_INTERVAL_MS)
  const openedView = useViewOpen()
  const open = useOpenFromSidebar()
  return views.map((view) => {
    const isOpen = isSelected && view.id === openedView
    const path = viewPath(instanceId, view.id)
    return (
      <SidebarMenuSubItem key={view.id}>
        <SidebarMenuSubButton
          isActive={isOpen}
          aria-current={isOpen ? "page" : undefined}
          href={path}
          onClick={(event) => {
            event.preventDefault()
            open(path)
          }}
        >
          <span>{view.title}</span>
        </SidebarMenuSubButton>
      </SidebarMenuSubItem>
    )
  })
}

/** The instance's most recent threads, then "All threads", with the one the URL opens marked. */
function RecentThreads({
  threads,
  instanceId,
  isSelected,
}: {
  threads: ThreadSummary[]
  instanceId: string
  isSelected: boolean
}) {
  const openedThread = useSelectedThreadId()
  const threadsOpen = useThreadsOpen()
  const open = useOpenFromSidebar()
  const allPath = threadsPath(instanceId)
  return (
    <>
      {threads.map((thread) => (
        <RecentThread
          key={thread.id}
          thread={thread}
          path={threadPath(instanceId, thread.id)}
          current={isSelected && thread.id === openedThread}
          onOpen={open}
        />
      ))}
      <SidebarMenuSubItem>
        {/* Archived threads and every routine run live on the thread list page. */}
        <SidebarMenuSubButton
          isActive={isSelected && threadsOpen}
          aria-current={isSelected && threadsOpen ? "page" : undefined}
          href={allPath}
          onClick={(event) => {
            event.preventDefault()
            open(allPath)
          }}
        >
          <span>All threads</span>
        </SidebarMenuSubButton>
      </SidebarMenuSubItem>
    </>
  )
}

/** A thread's link, named by its title, with its status icon and the status as its description. */
function RecentThread({
  thread,
  path,
  current,
  onOpen,
}: {
  thread: ThreadSummary
  path: string
  current: boolean
  onOpen: (path: string) => void
}) {
  const id = useId()
  const status = STATUS[thread.status]
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        isActive={current}
        aria-current={current ? "page" : undefined}
        aria-labelledby={`${id}-title`}
        aria-describedby={status === undefined ? undefined : `${id}-status`}
        href={path}
        onClick={(event) => {
          event.preventDefault()
          onOpen(path)
        }}
      >
        <span id={`${id}-title`} className="min-w-0 flex-1 truncate" title={threadTitle(thread)}>
          {threadTitle(thread)}
        </span>
        {status !== undefined && (
          <span id={`${id}-status`} className="flex shrink-0">
            <status.icon aria-hidden className={cn("size-3.5", status.className)} />
            <span className="sr-only">{status.label}</span>
          </span>
        )}
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  )
}
