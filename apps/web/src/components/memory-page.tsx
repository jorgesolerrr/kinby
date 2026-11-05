import {
  CallError,
  type Clock,
  type InstanceClient,
  type MemoryAddCommand,
  type MemoryListCommand,
  type MemoryListResult,
  type MemoryOpenResult,
  type NodeKind,
  type NodeSummary,
} from "@kinby/contract"
import { type FormEvent, useEffect, useId, useState, useSyncExternalStore } from "react"

import { Failure } from "@/components/config-alerts"
import { ProfileRow } from "@/components/profile-row"
import { SectionFrame } from "@/components/section-frame"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { usePace } from "@/hooks/use-pace"
import { pace, retried } from "@/lib/operation"
import { leaveNode, linkedNode, selectThread } from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"
import {
  BookOpenIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  CircleXIcon,
  FactoryIcon,
  LightbulbIcon,
  MessageSquareIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
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

/** A form's first field takes the focus when the form opens. */
const focus = (element: HTMLElement | null) => element?.focus()

/**
 * An instance's memory: the profile as one row, then the knowledge graph's nodes as rows, newest
 * first, narrowed by the filters above them. A row unfolds under itself to its node, where the user
 * corrects or forgets it, one row at a time. A link can name the node to unfold first. Add fact
 * opens a form at the top of the list. Each section reads when the page opens and after each write,
 * the graph also on Refresh, and neither polls.
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
    <div className="flex flex-col gap-8 py-6">
      <SectionFrame
        variant="page"
        title="Profile"
        lead="Your preferences and standing instructions. They go into every prompt."
      >
        <ProfileRow client={client} clock={clock} />
      </SectionFrame>
      <KnowledgeGraph client={client} clock={clock} instanceId={instanceId} />
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
  // Each Refresh and each write reads the list and the unfolded node again.
  const [reads, setReads] = useState(0)
  const [listed, setListed] = useState<MemoryListResult>()
  const [failure, setFailure] = useState<unknown>()
  const [selected, setSelected] = useState(linkedNode)
  const [adding, setAdding] = useState(false)
  // Whether a write found the unfolded node forgotten since the list was read.
  const [gone, setGone] = useState(false)

  useEffect(() => {
    const pacing = pace(clock)
    retried(() => client.call("memory.list", command(filters)), pacing).then(
      (page) => {
        if (pacing.stopped) return
        setListed(page)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (!pacing.stopped) setFailure(error)
      },
    )
    return pacing.stop
  }, [client, clock, filters, reads])

  const narrow = (changed: Partial<Filters>) => setFilters({ ...filters, ...changed })
  // Once the user moves off a linked node, a reload no longer unfolds it.
  const open = (node: string | undefined) => {
    setSelected(node)
    setGone(false)
    if (linkedNode() !== undefined) leaveNode(instanceId)
  }
  /** After a write, read the list again and unfold the fact it wrote, or nothing. */
  const written = (node: string | undefined) => {
    open(node)
    setReads(reads + 1)
  }
  const wentMissing = () => {
    written(undefined)
    setGone(true)
  }
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

  const items = listed?.items ?? []
  // A linked node the list doesn't hold, or one just written before the list is read again,
  // unfolds above the list. Its row keeps its key once the list holds it, so it reads only once.
  const rows: { node: string; summary?: NodeSummary }[] = [
    ...(selected !== undefined && !items.some((item) => item.node === selected)
      ? [{ node: selected }]
      : []),
    ...items.map((summary) => ({ node: summary.node, summary })),
  ]

  return (
    <SectionFrame
      variant="page"
      title="Knowledge graph"
      lead="The facts and episodes the instance remembers, newest first."
      actions={
        <Button size="sm" disabled={adding} onClick={() => setAdding(true)}>
          <PlusIcon data-icon="inline-start" />
          Add fact
        </Button>
      }
    >
      {adding && (
        <div className="rounded-xl border bg-muted/30 p-4">
          <FactForm
            label="Add fact"
            hint="Tell kinby something directly."
            action="Add fact"
            initial={NO_FACT}
            save={async (fact) => {
              const added = await client.call("memory.add", fact)
              setAdding(false)
              written(added.node)
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      )}
      <FilterBar filters={filters} onNarrow={narrow} onRefresh={() => setReads(reads + 1)} />
      {gone && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>That node is gone</AlertTitle>
          <AlertDescription>
            It was forgotten after the list was read, most likely by the agent.
          </AlertDescription>
        </Alert>
      )}
      {failure !== undefined && <Failure error={failure} />}
      {rows.length > 0 && (
        <ul aria-label="Facts and episodes" className="flex flex-col divide-y">
          {rows.map(({ node, summary }) => (
            <NodeRow
              key={node}
              client={client}
              clock={clock}
              instanceId={instanceId}
              node={node}
              summary={summary}
              reads={reads}
              open={node === selected}
              onOpenChange={(unfolded) => open(unfolded ? node : undefined)}
              onSubject={(subject) => narrow({ subject })}
              onWritten={written}
              onGone={wentMissing}
            />
          ))}
        </ul>
      )}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : listed.items.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing matches</EmptyTitle>
            <EmptyDescription>Search matches descriptions and subjects.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        listed.cursor !== null && (
          <Button variant="ghost" size="sm" onClick={() => void loadMore(listed)}>
            Load more
          </Button>
        )
      )}
    </SectionFrame>
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

function listedDetail({ date, subjects, source }: NodeSummary): string {
  return [date, subjects.join(", "), source === "user" && "added by you"]
    .filter(Boolean)
    .join(" · ")
}

function KindIcon({ kind }: { kind: NodeKind }) {
  return kind === "fact" ? <LightbulbIcon /> : <BookOpenIcon />
}

/**
 * One node's row: its kind, description and line, and a chevron that unfolds it. Unfolded, it
 * reads the node, and again on each of the list's reads. A row with no summary is a node the list
 * doesn't hold, named by its id until it is read.
 */
function NodeRow({
  client,
  clock,
  instanceId,
  node,
  summary,
  reads,
  open,
  onOpenChange,
  onSubject,
  onWritten,
  onGone,
}: {
  client: Caller
  clock: Clock
  instanceId: string
  node: string
  summary: NodeSummary | undefined
  reads: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubject: (subject: string) => void
  onWritten: (node: string | undefined) => void
  onGone: () => void
}) {
  const [opened, setOpened] = useState<MemoryOpenResult>()
  const [failure, setFailure] = useState<unknown>()

  useEffect(() => {
    if (!open) return
    const pacing = pace(clock)
    retried(() => client.call("memory.open", { node }), pacing).then(
      (read) => {
        if (pacing.stopped) return
        setOpened(read)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (!pacing.stopped) setFailure(error)
      },
    )
    return pacing.stop
  }, [client, clock, node, open, reads])

  const shown = summary ?? opened
  const description = shown?.description ?? node
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} render={<li aria-label={description} />}>
      <Item size="row">
        <ItemMedia variant="tile">
          {shown !== undefined && <KindIcon kind={shown.kind} />}
        </ItemMedia>
        <ItemContent className="min-w-0">
          <ItemTitle>{description}</ItemTitle>
          {shown !== undefined && (
            <ItemDescription lines="one">{listedDetail(shown)}</ItemDescription>
          )}
        </ItemContent>
        <ItemActions>
          <CollapsibleTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label="Details" />}
          >
            {open ? <ChevronUpIcon /> : <ChevronDownIcon />}
          </CollapsibleTrigger>
        </ItemActions>
      </Item>
      <CollapsibleContent>
        <div className="flex flex-col gap-3 pb-4 pl-11 text-sm">
          {failure !== undefined && <Failure error={failure} />}
          {opened === undefined ? (
            failure === undefined && <Skeleton className="h-24 w-full" />
          ) : (
            <NodeDetails
              key={reads}
              client={client}
              clock={clock}
              instanceId={instanceId}
              opened={opened}
              onSubject={onSubject}
              onWritten={onWritten}
              onGone={onGone}
            />
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/**
 * What an unfolded row shows of its node. A subject chip narrows the list to that subject. A fact
 * can be corrected, and any node forgotten. `onWritten` hears the fact a correction wrote, or
 * nothing after a forget, and `onGone` hears that the node was forgotten before the write reached
 * it.
 */
function NodeDetails({
  client,
  clock,
  instanceId,
  opened,
  onSubject,
  onWritten,
  onGone,
}: {
  client: Caller
  clock: Clock
  instanceId: string
  opened: MemoryOpenResult
  onSubject: (subject: string) => void
  onWritten: (node: string | undefined) => void
  onGone: () => void
}) {
  const { node } = opened
  const [correcting, setCorrecting] = useState(false)
  const [forgetting, setForgetting] = useState(false)
  const [failure, setFailure] = useState<unknown>()

  if (correcting) {
    return (
      <FactForm
        label="Correct fact"
        hint="Saving writes a new fact dated today and forgets this one."
        action="Save correction"
        initial={{
          description: opened.description,
          subjects: opened.subjects.join(", "),
          body: opened.body,
        }}
        save={async (fact) => {
          const corrected = await unlessGone(client.call("memory.correct", { node, ...fact }))
          if (corrected === "gone") onGone()
          else onWritten(corrected.node)
        }}
        onCancel={() => setCorrecting(false)}
      />
    )
  }
  const forget = async () => {
    setForgetting(true)
    setFailure(undefined)
    try {
      const forgotten = await unlessGone(client.call("memory.forget", { node }))
      if (forgotten === "gone") onGone()
      else onWritten(undefined)
    } catch (error) {
      setFailure(error)
      setForgetting(false)
    }
  }
  return (
    <>
      {failure !== undefined && <Failure error={failure} />}
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
      <p className="whitespace-pre-wrap">{opened.body}</p>
      {opened.tools !== null && opened.tools.length > 0 && (
        <p className="text-muted-foreground">Tool path: {opened.tools.join(" → ")}</p>
      )}
      <Origin client={client} clock={clock} instanceId={instanceId} opened={opened} />
      <div className="flex flex-wrap gap-2">
        {opened.kind === "fact" && (
          <Button
            variant="outline"
            size="sm"
            disabled={forgetting}
            onClick={() => setCorrecting(true)}
          >
            <PencilIcon data-icon="inline-start" />
            Correct
          </Button>
        )}
        <AlertDialog>
          <AlertDialogTrigger
            render={<Button variant="destructive" size="sm" disabled={forgetting} />}
          >
            {forgetting ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <Trash2Icon data-icon="inline-start" />
            )}
            Forget
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Forget this {opened.kind}?</AlertDialogTitle>
              <AlertDialogDescription>
                kinby stops using &quot;{opened.description}&quot;. Forgetting can&apos;t be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={() => void forget()}>
                Forget
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </>
  )
}

/** "gone" when the instance found no live node to write to, or what the write answered. */
async function unlessGone<T>(write: Promise<T>): Promise<T | "gone"> {
  try {
    return await write
  } catch (error) {
    if (error instanceof CallError && error.code === "NOT_FOUND") return "gone"
    throw error
  }
}

/** A fact as the user edits it. Subjects are one line, separated by commas. */
interface FactDraft {
  description: string
  subjects: string
  body: string
}

const NO_FACT: FactDraft = { description: "", subjects: "", body: "" }

function fact({ description, subjects, body }: FactDraft): MemoryAddCommand {
  return {
    description,
    subjects: subjects
      .split(",")
      .map((subject) => subject.trim())
      .filter(Boolean),
    body,
  }
}

/**
 * A fact's description, subjects, and body, saved through `save` by the `action` button. A value
 * the instance refuses shows its reason beside its field.
 */
function FactForm({
  label,
  hint,
  action,
  initial,
  save,
  onCancel,
}: {
  label: string
  hint: string
  action: string
  initial: FactDraft
  save: (fact: MemoryAddCommand) => Promise<void>
  onCancel: () => void
}) {
  const id = useId()
  const [draft, setDraft] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()

  const edit = (changed: Partial<FactDraft>) => {
    setDraft({ ...draft, ...changed })
    setErrors({})
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setFailure(undefined)
    try {
      await save(fact(draft))
    } catch (error) {
      if (error instanceof CallError && Object.keys(error.fields).length > 0) {
        setErrors(error.fields)
      } else setFailure(error)
    } finally {
      setSaving(false)
    }
  }
  const input = (name: keyof FactDraft) => ({
    id: `${id}-${name}`,
    value: draft[name],
    readOnly: saving,
    "aria-invalid": errors[name] !== undefined || undefined,
    onChange: (event: { target: { value: string } }) => edit({ [name]: event.target.value }),
  })

  return (
    <form
      aria-label={label}
      className="flex flex-col gap-3"
      onSubmit={(event) => void submit(event)}
    >
      <p className="text-sm text-muted-foreground">{hint}</p>
      {failure !== undefined && <Failure error={failure} />}
      <FieldGroup>
        <Field data-invalid={errors.description !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
          <Input ref={focus} {...input("description")} />
          <FieldError>{errors.description}</FieldError>
        </Field>
        <Field data-invalid={errors.subjects !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-subjects`}>Subjects</FieldLabel>
          <Input {...input("subjects")} />
          <FieldDescription>Separate subjects with commas.</FieldDescription>
          <FieldError>{errors.subjects}</FieldError>
        </Field>
        <Field data-invalid={errors.body !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-body`}>Body</FieldLabel>
          <Textarea className="min-h-32" {...input("body")} />
          <FieldError>{errors.body}</FieldError>
        </Field>
      </FieldGroup>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving || draft.description === ""}>
          {saving && <Spinner data-icon="inline-start" />}
          {action}
        </Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

/** Where the node came from, linking to the thread it was learned or recapped in. */
function Origin({
  client,
  clock,
  instanceId,
  opened,
}: {
  client: Caller
  clock: Clock
  instanceId: string
  opened: MemoryOpenResult
}) {
  const threads = threadList(client, "all")
  const listed = useSyncExternalStore(threads.onChange, threads.view)
  const threadId = opened.thread
  const thread = listed?.threads.find((summary) => summary.id === threadId)
  const missing = threadId !== null && thread === undefined
  // The sidebar lists the threads, but at phone width it is closed and nothing does.
  useEffect(() => {
    if (!missing) return
    const pacing = pace(clock)
    retried(() => threads.list(), pacing).catch(() => {})
    return pacing.stop
  }, [threads, clock, missing])

  if (opened.run !== null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <FactoryIcon className="size-4" />
        Recap of a factory run
      </p>
    )
  }
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
