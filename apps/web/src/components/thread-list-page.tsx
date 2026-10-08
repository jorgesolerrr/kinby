import { CallError } from "@kinby/contract"
import type {
  Clock,
  InstanceClient,
  ThreadListCommand,
  ThreadListResult,
  ThreadStatus,
  ThreadSummary,
} from "@kinby/contract"
import { type ReactNode, useEffect, useState } from "react"

import { ArchiveButton } from "@/components/archive-button"
import { Failure, OlderCoreAlert } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePace } from "@/hooks/use-pace"
import { when } from "@/lib/config-changes"
import { retried } from "@/lib/operation"
import { openPackage, selectThread, threadPath } from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"

type Caller = Pick<InstanceClient, "call">

/** How many threads a page asks for at a time. */
const PAGE_SIZE = 50

/** What the page lists: every thread, the archived ones, or one routine's runs. */
type Shown = "all" | "archived" | `${typeof ROUTINE}${string}`

const ROUTINE = "routine:"

const FILTERS: { value: Shown; label: string }[] = [
  { value: "all", label: "All threads" },
  { value: "archived", label: "Archived" },
]

/** The page shows every status, idle included, unlike the sidebar. */
const STATUS_BADGES: Record<ThreadStatus, ReactNode> = {
  idle: <Badge variant="outline">idle</Badge>,
  running: <Badge variant="secondary">working</Badge>,
  awaiting_approval: <Badge>needs you</Badge>,
  failed: <Badge variant="destructive">failed</Badge>,
}

/**
 * Every thread of an instance, routine runs and archived ones included, the most recently active
 * first. It lists all of them, the archived ones, or one routine's runs, a page at a time.
 */
export function ThreadListPage({
  client,
  clock,
  instanceId,
}: {
  client: Caller
  clock: Clock
  instanceId: string
}) {
  const pacing = usePace(clock)
  const [shown, setShown] = useState<Shown>("all")
  const [query, setQuery] = useState("")
  const [routines, setRoutines] = useState<string[]>([])
  const [listed, setListed] = useState<ThreadListResult>()
  const [failure, setFailure] = useState<unknown>()

  useEffect(() => {
    let current = true
    retried(() => client.call("thread.list", command(shown)), pacing).then(
      (page) => {
        if (!current) return
        setListed(page)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (current) setFailure(error)
      },
    )
    return () => {
      current = false
    }
  }, [client, pacing, shown])

  useEffect(() => {
    let current = true
    retried(() => client.call("routine.list", {}), pacing).then(
      (listed) => {
        if (current) setRoutines(listed.routines.map((routine) => routine.name))
      },
      // Without its routines, the filter offers all threads and the archived ones.
      () => {},
    )
    return () => {
      current = false
    }
  }, [client, pacing])

  const loadMore = async (latest: ThreadListResult) => {
    if (latest.cursor === null) return
    try {
      const next = await retried(
        () => client.call("thread.list", { ...command(shown), cursor: latest.cursor }),
        pacing,
      )
      // A page read for a list that changed meanwhile belongs to no list shown.
      setListed((current) =>
        current === latest ? { ...next, threads: [...latest.threads, ...next.threads] } : current,
      )
      setFailure(undefined)
    } catch (error) {
      setFailure(error)
    }
  }

  const toggleArchive = async (thread: ThreadSummary) => {
    try {
      const changed = await client.call(thread.archived ? "thread.unarchive" : "thread.archive", {
        thread_id: thread.id,
      })
      // The row stays where it is, whatever the filter, so a slip is one click to undo.
      setListed(
        (current) =>
          current && {
            ...current,
            threads: current.threads.map((listed) => (listed.id === changed.id ? changed : listed)),
          },
      )
      setFailure(undefined)
      await threadList(client, "sidebar").list()
    } catch (error) {
      setFailure(error)
    }
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Threads</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="search"
            className="w-64"
            aria-label="Search titles"
            placeholder="Search titles"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <ShownPicker shown={shown} routines={routines} onPick={setShown} />
        </div>
      </div>
      {failure !== undefined &&
        (fromOlderCore(failure) ? (
          <OlderCoreAlert onOpenUpdate={() => openPackage(instanceId)} />
        ) : (
          <Failure error={failure} />
        ))}
      {listed === undefined && failure === undefined && <Skeleton className="h-72 w-full" />}
      {listed !== undefined && (
        <>
          <ThreadTable
            instanceId={instanceId}
            threads={matching(listed.threads, query)}
            onToggleArchive={toggleArchive}
          />
          {listed.cursor !== null && (
            <Button variant="ghost" size="sm" onClick={() => void loadMore(listed)}>
              Load more
            </Button>
          )}
        </>
      )}
    </div>
  )
}

/** The threads whose title holds `query`, ignoring case. The search reads only loaded pages. */
function matching(threads: ThreadSummary[], query: string): ThreadSummary[] {
  const wanted = query.trim().toLocaleLowerCase()
  return threads.filter((thread) => threadTitle(thread).toLocaleLowerCase().includes(wanted))
}

/** A core from before paging refuses the page's thread.list, whose command it does not know. */
function fromOlderCore(error: unknown): boolean {
  return error instanceof CallError && error.code === "INVALID_ARGUMENT"
}

function command(shown: Shown): ThreadListCommand {
  if (shown === "all" || shown === "archived") return { filter: shown, limit: PAGE_SIZE }
  return { filter: "all", routine: shown.slice(ROUTINE.length), limit: PAGE_SIZE }
}

function ShownPicker({
  shown,
  routines,
  onPick,
}: {
  shown: Shown
  routines: string[]
  onPick: (shown: Shown) => void
}) {
  const runs = routines.map((name) => ({ value: `${ROUTINE}${name}` as const, label: name }))
  return (
    <Select<Shown>
      items={[...FILTERS, ...runs]}
      value={shown}
      onValueChange={(picked) => picked !== null && onPick(picked)}
    >
      <SelectTrigger size="sm" aria-label="Show" className="w-48">
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end" alignItemWithTrigger={false}>
        <SelectGroup>
          {FILTERS.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectGroup>
        {runs.length > 0 && (
          <>
            <SelectSeparator />
            <SelectGroup>
              <SelectLabel>Routine runs</SelectLabel>
              {runs.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </>
        )}
      </SelectContent>
    </Select>
  )
}

/** A click anywhere on a row opens its thread, but for its archive button. The title is its link. */
function ThreadTable({
  instanceId,
  threads,
  onToggleArchive,
}: {
  instanceId: string
  threads: ThreadSummary[]
  onToggleArchive: (thread: ThreadSummary) => Promise<void>
}) {
  return (
    <Table aria-label="Threads">
      <TableHeader>
        <TableRow>
          <TableHead>Title</TableHead>
          <TableHead>Origin</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Last activity</TableHead>
          <TableHead>
            <span className="sr-only">Archive</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {threads.map((thread) => (
          <TableRow
            key={thread.id}
            className="cursor-pointer"
            onClick={() => selectThread(instanceId, thread.id)}
          >
            <TableCell className="w-full max-w-0">
              {/* The row opens the thread, so the link only keeps the browser from loading it. */}
              <a
                className="block truncate"
                href={threadPath(instanceId, thread.id)}
                onClick={(event) => event.preventDefault()}
              >
                {threadTitle(thread)}
              </a>
            </TableCell>
            <TableCell>{originName(thread.origin)}</TableCell>
            <TableCell>{STATUS_BADGES[thread.status]}</TableCell>
            <TableCell>{when(thread.last_activity_at)}</TableCell>
            <TableCell onClick={(event) => event.stopPropagation()}>
              <ArchiveButton archived={thread.archived} onToggle={() => onToggleArchive(thread)} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function originName(origin: ThreadSummary["origin"]): string {
  switch (origin.kind) {
    case "user":
      return "You"
    case "routine":
      return origin.name
    case "factory-run":
      return `${origin.factory} · ${origin.step}`
  }
}
