import type {
  Clock,
  InstanceClient,
  MemoryListCommand,
  MemoryListResult,
  MemoryOpenResult,
  NodeKind,
  NodeSummary,
} from "@kinby/contract"
import { useEffect, useId, useState, useSyncExternalStore } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { usePace } from "@/hooks/use-pace"
import { type Pace, retried } from "@/lib/operation"
import { selectThread } from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"
import {
  BookOpenIcon,
  LightbulbIcon,
  MessageSquareIcon,
  RefreshCwIcon,
  UserIcon,
  XIcon,
} from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** What the list is narrowed to. An empty string or an absent value narrows nothing. */
interface Filters {
  query: string
  kind: NodeKind | undefined
  subject: string | undefined
  after: string
  before: string
}

const NO_FILTERS: Filters = {
  query: "",
  kind: undefined,
  subject: undefined,
  after: "",
  before: "",
}

const KINDS: Record<NodeKind | "all", string> = { all: "All", fact: "Facts", episode: "Episodes" }

/**
 * An instance's memory, as two tabs. The knowledge graph lists its nodes newest first, narrowed by
 * the filters above it, and opens one in the pane beside it. The page reads when it opens and on
 * Refresh, and never polls. The profile tab is not built yet.
 */
export function MemoryPage({
  client,
  clock,
  instanceId,
}: {
  client: Caller
  clock: Clock
  instanceId: string
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-6">
      <Tabs defaultValue="graph" className="min-h-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">Memory</h1>
          <TabsList>
            <TabsTrigger value="profile" disabled>
              Profile
            </TabsTrigger>
            <TabsTrigger value="graph">Knowledge graph</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="graph" className="flex min-h-0 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-4 pt-2">
            <KnowledgeGraph client={client} clock={clock} instanceId={instanceId} />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function KnowledgeGraph({
  client,
  clock,
  instanceId,
}: {
  client: Caller
  clock: Clock
  instanceId: string
}) {
  const pacing = usePace(clock)
  const [filters, setFilters] = useState(NO_FILTERS)
  // Each Refresh reads the list and the opened node again.
  const [reads, setReads] = useState(0)
  const [listed, setListed] = useState<MemoryListResult>()
  const [failure, setFailure] = useState<unknown>()
  const [selected, setSelected] = useState<string>()

  useEffect(() => {
    let current = true
    retried(() => client.call("memory.list", command(filters)), pacing).then(
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
  }, [client, pacing, filters, reads])

  const narrow = (changed: Partial<Filters>) => setFilters({ ...filters, ...changed })
  const loadMore = async (shown: MemoryListResult) => {
    if (shown.cursor === null) return
    try {
      const next = await retried(
        () => client.call("memory.list", { ...command(filters), cursor: shown.cursor }),
        pacing,
      )
      // A page read for a list that changed meanwhile belongs to no list shown.
      setListed((latest) =>
        latest === shown ? { items: [...shown.items, ...next.items], cursor: next.cursor } : latest,
      )
    } catch (error) {
      setFailure(error)
    }
  }

  return (
    <>
      <FilterBar filters={filters} onNarrow={narrow} onRefresh={() => setReads(reads + 1)} />
      {failure !== undefined && <Failure error={failure} />}
      <div className="flex min-h-0 flex-1 flex-col gap-6 md:flex-row">
        <div className="min-w-0 overflow-y-auto md:w-1/2">
          {listed === undefined ? (
            failure === undefined && <Skeleton className="h-72 w-full" />
          ) : (
            <NodeList
              listed={listed}
              selected={selected}
              onSelect={setSelected}
              onLoadMore={() => void loadMore(listed)}
            />
          )}
        </div>
        <section
          aria-label="Opened node"
          className="min-w-0 overflow-y-auto md:w-1/2 md:border-l md:pl-6"
        >
          {selected === undefined ? (
            <p className="text-sm text-muted-foreground">Open a node to read it.</p>
          ) : (
            <NodePane
              key={`${selected} ${reads}`}
              client={client}
              pacing={pacing}
              instanceId={instanceId}
              node={selected}
              onSubject={(subject) => narrow({ subject })}
            />
          )}
        </section>
      </div>
    </>
  )
}

function command({ query, kind, subject, after, before }: Filters): MemoryListCommand {
  return {
    query,
    ...(kind !== undefined && { kind }),
    ...(subject !== undefined && { subject }),
    ...(after !== "" && { after }),
    ...(before !== "" && { before }),
  }
}

function FilterBar({
  filters,
  onNarrow,
  onRefresh,
}: {
  filters: Filters
  onNarrow: (changed: Partial<Filters>) => void
  onRefresh: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        type="search"
        className="w-64"
        aria-label="Search"
        placeholder="Search descriptions and subjects"
        value={filters.query}
        onChange={(event) => onNarrow({ query: event.target.value })}
      />
      <ToggleGroup
        aria-label="Kind"
        size="sm"
        variant="outline"
        value={[filters.kind ?? "all"]}
        onValueChange={([kind]) => {
          if (kind === "fact" || kind === "episode") onNarrow({ kind })
          else if (kind === "all") onNarrow({ kind: undefined })
        }}
      >
        {Object.entries(KINDS).map(([kind, label]) => (
          <ToggleGroupItem key={kind} value={kind}>
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Input
        type="date"
        className="w-40"
        aria-label="After"
        value={filters.after}
        onChange={(event) => onNarrow({ after: event.target.value })}
      />
      <Input
        type="date"
        className="w-40"
        aria-label="Before"
        value={filters.before}
        onChange={(event) => onNarrow({ before: event.target.value })}
      />
      {filters.subject !== undefined && (
        <Badge
          variant="secondary"
          render={<button type="button" aria-label={`Stop filtering by ${filters.subject}`} />}
          onClick={() => onNarrow({ subject: undefined })}
        >
          {filters.subject}
          <XIcon data-icon="inline-end" />
        </Badge>
      )}
      <Button size="sm" variant="ghost" onClick={onRefresh}>
        <RefreshCwIcon data-icon="inline-start" />
        Refresh
      </Button>
    </div>
  )
}

function NodeList({
  listed,
  selected,
  onSelect,
  onLoadMore,
}: {
  listed: MemoryListResult
  selected: string | undefined
  onSelect: (node: string) => void
  onLoadMore: () => void
}) {
  if (listed.items.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>Nothing matches</EmptyTitle>
          <EmptyDescription>Search matches descriptions and subjects.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <ItemGroup aria-label="Nodes">
        {listed.items.map((node) => (
          <li key={node.node} className="list-none">
            <Item
              size="sm"
              variant={node.node === selected ? "muted" : "default"}
              render={
                <button
                  type="button"
                  aria-label={node.description}
                  aria-current={node.node === selected || undefined}
                  onClick={() => onSelect(node.node)}
                />
              }
            >
              <ItemMedia variant="icon">
                <KindIcon kind={node.kind} />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{node.description}</ItemTitle>
                <ItemDescription>{listedDetail(node)}</ItemDescription>
              </ItemContent>
            </Item>
          </li>
        ))}
      </ItemGroup>
      {listed.cursor !== null && (
        <Button variant="ghost" size="sm" onClick={onLoadMore}>
          Load more
        </Button>
      )}
    </div>
  )
}

function listedDetail({ date, subjects, source }: NodeSummary): string {
  return [date, subjects.join(", "), source === "user" && "added by you"]
    .filter(Boolean)
    .join(" · ")
}

function KindIcon({ kind }: { kind: NodeKind }) {
  return kind === "fact" ? <LightbulbIcon /> : <BookOpenIcon />
}

/** One node, read when it opens. A subject chip narrows the list to that subject. */
function NodePane({
  client,
  pacing,
  instanceId,
  node,
  onSubject,
}: {
  client: Caller
  pacing: Pace
  instanceId: string
  node: string
  onSubject: (subject: string) => void
}) {
  const [opened, setOpened] = useState<MemoryOpenResult>()
  const [failure, setFailure] = useState<unknown>()
  const headingId = useId()

  useEffect(() => {
    let current = true
    retried(() => client.call("memory.open", { node }), pacing).then(
      (read) => {
        if (current) setOpened(read)
      },
      (error: unknown) => {
        if (current) setFailure(error)
      },
    )
    return () => {
      current = false
    }
  }, [client, pacing, node])

  if (failure !== undefined) return <Failure error={failure} />
  if (opened === undefined) return <Skeleton className="h-48 w-full" />
  return (
    <article aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{opened.kind}</Badge>
        <span className="text-sm text-muted-foreground">{opened.date}</span>
      </div>
      <h2 id={headingId} className="text-lg font-semibold">
        {opened.description}
      </h2>
      {opened.subjects.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {opened.subjects.map((subject) => (
            <Badge
              key={subject}
              variant="secondary"
              render={<button type="button" aria-label={subject} />}
              onClick={() => onSubject(subject)}
            >
              {subject}
            </Badge>
          ))}
        </div>
      )}
      <p className="text-sm whitespace-pre-wrap">{opened.body}</p>
      {opened.tools !== null && (
        <p className="text-sm text-muted-foreground">Tool path: {opened.tools.join(" → ")}</p>
      )}
      <Separator />
      <Origin client={client} pacing={pacing} instanceId={instanceId} opened={opened} />
    </article>
  )
}

/** Where the node came from, linking to the thread it was learned or recapped in. */
function Origin({
  client,
  pacing,
  instanceId,
  opened,
}: {
  client: Caller
  pacing: Pace
  instanceId: string
  opened: MemoryOpenResult
}) {
  const threads = threadList(client)
  const listed = useSyncExternalStore(threads.onChange, threads.view)
  const threadId = opened.thread
  const thread = listed?.threads.find((summary) => summary.id === threadId)
  const missing = threadId !== null && thread === undefined
  // The sidebar lists the threads, but at phone width it is closed and nothing does.
  useEffect(() => {
    if (missing) retried(() => threads.list(), pacing).catch(() => {})
  }, [threads, pacing, missing])

  if (opened.source === "user" || threadId === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <UserIcon className="size-4" />
        Added by you
      </p>
    )
  }
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <MessageSquareIcon className="size-4" />
      {opened.kind === "episode" ? "Recap of a turn in" : "Remembered in"}
      <Button variant="link" size="sm" onClick={() => selectThread(instanceId, threadId)}>
        {thread === undefined ? "its thread" : threadTitle(thread)}
      </Button>
    </p>
  )
}
