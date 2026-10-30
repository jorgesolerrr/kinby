import type { Clock, InstanceClient, InstanceSummary, ThreadSummary } from "@kinby/contract"
import {
  BellDotIcon,
  BrainIcon,
  ChartColumnIcon,
  CircleAlertIcon,
  HistoryIcon,
  LoaderCircleIcon,
  type LucideIcon,
  MessageSquarePlusIcon,
  MousePointerClickIcon,
  SearchIcon,
  Settings2Icon,
  ShieldAlertIcon,
} from "lucide-react"
import { cn } from "cn"
import { useId, useState, useSyncExternalStore } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Button, buttonVariants } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { usePolled } from "@/hooks/use-polled"
import { instanceName } from "@/lib/instances"
import {
  configPath,
  memoryPath,
  navigate,
  selectThread,
  statsPath,
  threadPath,
  threadsPath,
  useInstanceTab,
  useSelectedThreadId,
} from "@/lib/selection"
import { byDay, matching, needsYou, threadList, threadTitle } from "@/lib/thread-list"

/** How often the threads are listed again while the page is visible. */
const LIST_INTERVAL_MS = 5_000

/** Which threads the column shows: the sidebar set, the ones in it that need the user, or routine runs. */
type Shown = "all" | "needs" | "routines"

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

type Client = Pick<InstanceClient, "call" | "state" | "onStateChange">

/**
 * A running instance's threads beside the sidebar: its header with its pages, a search, New
 * thread, a filter, and the threads grouped by the day they were last active. `onPicked` runs once
 * a thread or page opens, so a sheet holding the column can close.
 */
export function ThreadsColumn({
  client,
  clock,
  instance,
  onPicked,
}: {
  client: Client
  clock: Clock
  instance: InstanceSummary
  onPicked?: () => void
}) {
  const instanceId = instance.instance_id
  const name = instanceName(instance)
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const [shown, setShown] = useState<Shown>("all")
  const [query, setQuery] = useState("")
  // The sidebar set is read throughout, as the instance's row counts what needs the user from it.
  // Routine runs are mostly quiet, so it leaves them out, and the all set has them.
  const sidebar = threadList(client, "sidebar")
  const all = threadList(client, "all")
  usePolled(sidebar.list, clock, connected, LIST_INTERVAL_MS)
  usePolled(all.list, clock, connected && shown === "routines", LIST_INTERVAL_MS)
  const list = shown === "routines" ? all : sidebar
  const listed = useSyncExternalStore(list.onChange, list.view)?.threads
  const selectedId = useSelectedThreadId()
  const tab = useInstanceTab()

  const kept = listed?.filter((thread) =>
    shown === "needs"
      ? needsYou(thread)
      : shown === "routines"
        ? thread.origin.kind === "routine" && !thread.archived
        : true,
  )
  const threads = kept && matching(kept, query)

  const open = (path: string) => {
    navigate(path)
    onPicked?.()
  }
  const startThread = () =>
    client
      .call("thread.create", {})
      .then((created) => {
        selectThread(instanceId, created.id)
        onPicked?.()
        return list.listKept()
      })
      // A dropped socket shows as reconnecting, and the next poll lists a thread that did start.
      .catch(() => {})

  return (
    <aside aria-label={`${name}'s threads`} className="flex min-h-0 w-full flex-1 flex-col">
      <div className="flex flex-col gap-3 border-b p-3">
        <div className="flex items-center gap-2">
          <InstanceAvatar avatar={instance.avatar} name={name} />
          <div className="flex min-w-0 flex-1 flex-col">
            <h2 className="truncate font-heading font-semibold">{name}</h2>
            {instance.persona_name !== null && (
              <p className="truncate font-mono text-xs text-muted-foreground">
                {instance.manifest_id}
              </p>
            )}
          </div>
          <PageLink
            label="Memory"
            icon={BrainIcon}
            path={memoryPath(instanceId)}
            current={tab === "memory"}
            onOpen={open}
          />
          <PageLink
            label="Stats"
            icon={ChartColumnIcon}
            path={statsPath(instanceId)}
            current={tab === "stats"}
            onOpen={open}
          />
          <PageLink
            label="Configure"
            icon={Settings2Icon}
            path={configPath(instanceId)}
            current={tab === "config"}
            onOpen={open}
          />
        </div>
        <div className="flex gap-2">
          <InputGroup>
            <InputGroupInput
              type="search"
              aria-label="Search threads"
              placeholder="Search threads"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
          </InputGroup>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  size="icon"
                  aria-label="New thread"
                  disabled={!connected}
                  onClick={() => void startThread()}
                />
              }
            >
              <MessageSquarePlusIcon />
            </TooltipTrigger>
            <TooltipContent>New thread</TooltipContent>
          </Tooltip>
        </div>
        <ToggleGroup
          aria-label="Show"
          variant="outline"
          size="sm"
          value={[shown]}
          onValueChange={([picked]) => setShown((picked as Shown | undefined) ?? "all")}
        >
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          <ToggleGroupItem value="needs">Needs you</ToggleGroupItem>
          <ToggleGroupItem value="routines">Routines</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <nav aria-label="Threads" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2">
        {threads !== undefined &&
          (threads.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>{nothingShown(shown, query)}</EmptyTitle>
                {shown === "all" && query.trim() === "" && (
                  <EmptyDescription>Start one and {name} picks it up.</EmptyDescription>
                )}
              </EmptyHeader>
            </Empty>
          ) : (
            byDay(threads, new Date()).map(({ day, threads }) => (
              <section key={day} aria-label={day} className="flex flex-col gap-0.5">
                <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">{day}</h3>
                {threads.map(({ thread, time }) => (
                  <ThreadRow
                    key={thread.id}
                    thread={thread}
                    time={time}
                    href={threadPath(instanceId, thread.id)}
                    current={thread.id === selectedId}
                    onOpen={open}
                  />
                ))}
              </section>
            ))
          ))}
      </nav>
      <div className="border-t p-2">
        {/* Archived threads and every routine run live on the thread list page. */}
        <a
          href={threadsPath(instanceId)}
          className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-full justify-start")}
          onClick={(event) => {
            event.preventDefault()
            open(threadsPath(instanceId))
          }}
        >
          <HistoryIcon data-icon="inline-start" />
          All threads and archived
        </a>
      </div>
    </aside>
  )
}

function nothingShown(shown: Shown, query: string): string {
  if (query.trim() !== "") return `No thread matches "${query.trim()}"`
  if (shown === "needs") return "Nothing needs you"
  if (shown === "routines") return "No routine runs yet"
  return "No threads yet"
}

/** One of the instance's pages, as an icon with its name in a tooltip. */
function PageLink({
  label,
  icon: Icon,
  path,
  current,
  onOpen,
}: {
  label: string
  icon: LucideIcon
  path: string
  current: boolean
  onOpen: (path: string) => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <a
            href={path}
            aria-label={label}
            aria-current={current ? "page" : undefined}
            className={buttonVariants({
              variant: current ? "secondary" : "ghost",
              size: "icon-sm",
            })}
            onClick={(event) => {
              event.preventDefault()
              onOpen(path)
            }}
          />
        }
      >
        <Icon />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/**
 * A thread's link, named by its title, with its status and time as its description. A routine run
 * that needs no look shows a muted dot.
 */
function ThreadRow({
  thread,
  time,
  href,
  current,
  onOpen,
}: {
  thread: ThreadSummary
  time: string
  href: string
  current: boolean
  onOpen: (path: string) => void
}) {
  const id = useId()
  const status = STATUS[thread.status]
  const said = status?.label ?? (thread.origin.kind === "routine" ? "Routine run" : undefined)
  return (
    <a
      href={href}
      aria-labelledby={`${id}-title`}
      aria-describedby={said === undefined ? `${id}-time` : `${id}-status ${id}-time`}
      aria-current={current ? "page" : undefined}
      onClick={(event) => {
        event.preventDefault()
        onOpen(href)
      }}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent aria-[current=page]:text-accent-foreground"
    >
      {status !== undefined ? (
        <status.icon aria-hidden className={cn("size-3.5 shrink-0", status.className)} />
      ) : (
        <span aria-hidden className="grid size-3.5 shrink-0 place-items-center">
          {said !== undefined && <span className="size-2 rounded-full bg-muted-foreground" />}
        </span>
      )}
      {said !== undefined && (
        <span id={`${id}-status`} className="sr-only">
          {said}
        </span>
      )}
      <span
        id={`${id}-title`}
        className="min-w-0 flex-1 truncate font-medium"
        title={threadTitle(thread)}
      >
        {threadTitle(thread)}
      </span>
      <span id={`${id}-time`} className="shrink-0 font-mono text-xs text-muted-foreground">
        {time}
      </span>
    </a>
  )
}
