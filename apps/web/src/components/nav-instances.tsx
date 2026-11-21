import type { Clock, InstanceClient, InstanceSummary, ThreadSummary } from "@kinby/contract"
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
import { useId, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
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
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { useSidebarThreads } from "@/hooks/use-sidebar-threads"
import { INSTANCE_TABS, instanceName, shownState } from "@/lib/instances"
import {
  CREATE_PATH,
  homePath,
  screenPath,
  tabPath,
  threadPath,
  threadsPath,
  useInstanceTab,
  useScreenOpen,
  useSelectedThreadId,
  useThreadsOpen,
} from "@/lib/selection"
import { needsYou, threadTitle } from "@/lib/thread-list"

export type Reached = Pick<InstanceClient, "call" | "state" | "onStateChange">

/** How many of an instance's most recent threads its group lists. */
const RECENT = 4

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
  clock,
  reached,
  folds,
  onFold,
}: {
  /** Home first. */
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  clock: Clock
  /** The connection each running instance is reached on, by instance. */
  reached: ReadonlyMap<string, Reached>
  /** Kept above the phone's drawer, which unmounts the groups each time it closes. */
  folds: Folds
  onFold: (instanceId: string, open: boolean) => void
}) {
  const open = useOpenFromSidebar()
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        {instances.map((instance) => (
          <InstanceGroup
            key={instance.instance_id}
            instance={instance}
            isSelected={instance === selected}
            clock={clock}
            reached={reached.get(instance.instance_id)}
            folded={folds[instance.instance_id]}
            onFold={(open) => onFold(instance.instance_id, open)}
          />
        ))}
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
    </SidebarGroup>
  )
}

/**
 * An instance's row, which opens its screen, its chevron, which folds the group under it, and its
 * menu. The row says when the instance is not running, and shows an ember dot while its sidebar
 * threads need the user. Under a running instance, listed on the connection `reached`, come its
 * most recent sidebar threads and "All threads".
 */
function InstanceGroup({
  instance,
  isSelected,
  clock,
  reached,
  folded,
  onFold,
}: {
  instance: InstanceSummary
  isSelected: boolean
  clock: Clock
  reached: Reached | undefined
  folded: boolean | undefined
  onFold: (open: boolean) => void
}) {
  const name = instanceName(instance)
  const running = instance.process === "running"
  const threads = useSidebarThreads(reached, clock)
  const count = (threads ?? []).filter(needsYou).length
  // A group opens by itself until the user folds or unfolds it, and the user's choice then holds.
  const [opened, setOpened] = useState(false)
  if (!opened && (instance.home || count > 0 || isSelected)) setOpened(true)
  const open = folded ?? opened
  const tab = useInstanceTab()
  const screenOpen = useScreenOpen()
  const openPage = useOpenFromSidebar()
  const path = screenPath(instance.instance_id)
  const current = isSelected && (tab !== undefined || screenOpen)
  return (
    <Collapsible open={open} onOpenChange={onFold} render={<SidebarMenuItem />}>
      <SidebarMenuButton
        isActive={current}
        aria-current={current ? "page" : undefined}
        className="group-has-data-[sidebar=menu-action]/menu-item:pr-14"
        onClick={(event) => {
          event.preventDefault()
          openPage(path)
        }}
        render={
          <a href={path}>
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
          </a>
        }
      />
      <CollapsibleTrigger render={<SidebarMenuAction aria-label={`${name}'s threads`} />}>
        <ChevronRightIcon className={cn("transition-transform", open && "rotate-90")} />
      </CollapsibleTrigger>
      <InstanceMenu instanceId={instance.instance_id} name={name} running={running} />
      {reached !== undefined && (
        <CollapsibleContent>
          <SidebarMenuSub>
            {threads?.length === 0 && <NoThreads instanceId={instance.instance_id} />}
            <RecentThreads
              threads={(threads ?? []).slice(0, RECENT)}
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
        render={
          <SidebarMenuAction showOnHover aria-label={`Menu for ${name}`} className="right-7" />
        }
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

/** The muted row of a group with no threads, opening the home screen sent to it. */
function NoThreads({ instanceId }: { instanceId: string }) {
  const open = useOpenFromSidebar()
  const path = homePath(instanceId)
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        variant="muted"
        href={path}
        onClick={(event) => {
          event.preventDefault()
          open(path)
        }}
      >
        <span>No threads yet</span>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  )
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
