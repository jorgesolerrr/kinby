// PROTOTYPE, throwaway. Pieces the memory variants share: browsing state, filters, the node pane,
// the fact form, the profile editor, and the state panel.
import { useEffect, useState } from "react"

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
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
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
import { Textarea } from "@/components/ui/textarea"
import {
  BookOpenIcon,
  LightbulbIcon,
  MessageSquareIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  UserIcon,
} from "lucide-react"

import { type Filters, type Memory, NO_FILTERS, tokensOf } from "./flow"
import type { MemoryNode } from "./stub"

export function useBrowse(memory: Memory) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [pages, setPages] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const { items, more } = memory.list(filters, pages)
  const key = JSON.stringify(filters)
  useEffect(() => memory.logList(filters, pages), [key, pages]) // eslint-disable-line
  const update = (f: Partial<Filters>) => {
    setFilters({ ...filters, ...f })
    setPages(1)
  }
  return {
    filters,
    update,
    items,
    more,
    loadMore: () => setPages(pages + 1),
    selected,
    select: setSelected,
    refresh: () => memory.refresh(filters),
  }
}
export type Browse = ReturnType<typeof useBrowse>

export function FilterBar({ browse, compact }: { browse: Browse; compact?: boolean }) {
  const { filters, update } = browse
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        className={compact ? "w-full" : "w-64"}
        placeholder="Search descriptions and subjects"
        value={filters.query}
        onChange={(e) => update({ query: e.target.value })}
      />
      <div className="flex gap-1">
        {(["all", "fact", "episode"] as const).map((k) => (
          <Button
            key={k}
            size="sm"
            variant={filters.kind === k ? "secondary" : "ghost"}
            onClick={() => update({ kind: k })}
          >
            {k === "all" ? "All" : k === "fact" ? "Facts" : "Episodes"}
          </Button>
        ))}
      </div>
      {!compact && (
        <>
          <Input
            type="date"
            className="w-36"
            aria-label="After"
            value={filters.after}
            onChange={(e) => update({ after: e.target.value })}
          />
          <Input
            type="date"
            className="w-36"
            aria-label="Before"
            value={filters.before}
            onChange={(e) => update({ before: e.target.value })}
          />
        </>
      )}
      {filters.subject && (
        <Badge
          variant="secondary"
          render={<button type="button" />}
          onClick={() => update({ subject: null })}
        >
          {filters.subject} ×
        </Badge>
      )}
      <Button size="sm" variant="ghost" onClick={browse.refresh}>
        <RefreshCwIcon /> Refresh
      </Button>
    </div>
  )
}

export const KindIcon = ({ n }: { n: MemoryNode }) =>
  n.kind === "fact" ? <LightbulbIcon /> : <BookOpenIcon />

export function NodeList({ browse }: { browse: Browse }) {
  if (browse.items.length === 0)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>Nothing matches</EmptyTitle>
          <EmptyDescription>Search matches descriptions and subjects.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <ItemGroup>
      {browse.items.map((n) => (
        <Item
          key={n.node}
          size="sm"
          variant={n.node === browse.selected ? "muted" : "default"}
          render={<button type="button" onClick={() => browse.select(n.node)} />}
        >
          <ItemMedia variant="icon">
            <KindIcon n={n} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{n.description}</ItemTitle>
            <ItemDescription>
              {n.date} · {n.subjects.join(", ")}
              {n.source === "user" ? " · added by you" : ""}
            </ItemDescription>
          </ItemContent>
        </Item>
      ))}
      {browse.more && (
        <Button variant="ghost" size="sm" onClick={browse.loadMore}>
          Load more
        </Button>
      )}
    </ItemGroup>
  )
}

type FactDraft = { description: string; subjects: string[]; body: string }

export function FactForm({
  initial,
  submit,
  cancel,
  label,
}: {
  initial?: FactDraft
  submit: (f: FactDraft) => void
  cancel: () => void
  label: string
}) {
  const [description, setDescription] = useState(initial?.description ?? "")
  const [subjects, setSubjects] = useState(initial?.subjects.join(", ") ?? "")
  const [body, setBody] = useState(initial?.body ?? "")
  return (
    <FieldGroup>
      <Field>
        <FieldLabel>Description</FieldLabel>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field>
        <FieldLabel>Subjects</FieldLabel>
        <Input value={subjects} onChange={(e) => setSubjects(e.target.value)} />
        <FieldDescription>Comma separated. The latest fact about a subject wins.</FieldDescription>
      </Field>
      <Field>
        <FieldLabel>Body</FieldLabel>
        <Textarea value={body} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <div className="flex gap-2">
        <Button
          disabled={!description.trim()}
          onClick={() =>
            submit({
              description: description.trim(),
              subjects: subjects
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
              body,
            })
          }
        >
          {label}
        </Button>
        <Button variant="ghost" onClick={cancel}>
          Cancel
        </Button>
      </div>
    </FieldGroup>
  )
}

export function NodeDetail({ memory, browse, id }: { memory: Memory; browse: Browse; id: string }) {
  const [correcting, setCorrecting] = useState(false)
  const [n, setN] = useState<MemoryNode | undefined>()
  useEffect(() => {
    setN(memory.open(id))
    setCorrecting(false)
  }, [id]) // eslint-disable-line
  if (!n) return null
  if (correcting)
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Writes a new fact dated today and forgets this one.
        </p>
        <FactForm
          initial={n}
          label="Save correction"
          cancel={() => setCorrecting(false)}
          submit={(f) => browse.select(memory.correct(n.node, f))}
        />
      </div>
    )
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{n.kind}</Badge>
        <span className="text-sm text-muted-foreground">{n.date}</span>
      </div>
      <h2 className="text-lg font-semibold">{n.description}</h2>
      <div className="flex flex-wrap gap-1">
        {n.subjects.map((s) => (
          <Badge
            key={s}
            variant="secondary"
            render={<button type="button" />}
            onClick={() => browse.update({ subject: s })}
          >
            {s}
          </Badge>
        ))}
      </div>
      <p className="text-sm whitespace-pre-wrap">{n.body}</p>
      {n.tools && <p className="text-sm text-muted-foreground">Tool path: {n.tools.join(" → ")}</p>}
      <Separator />
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        {n.source === "user" ? (
          <>
            <UserIcon className="size-4" /> Added by you
          </>
        ) : (
          <>
            <MessageSquareIcon className="size-4" />
            {n.kind === "episode" ? "Recap of a turn in" : "Remembered in"}
            <Button variant="link" size="sm" className="px-0">
              {n.thread?.title}
            </Button>
          </>
        )}
      </p>
      <div className="flex gap-2">
        {n.kind === "fact" && (
          <Button variant="outline" size="sm" onClick={() => setCorrecting(true)}>
            <PencilIcon /> Correct
          </Button>
        )}
        <AlertDialog>
          <AlertDialogTrigger render={<Button variant="outline" size="sm" />}>
            <Trash2Icon /> Forget
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Forget this {n.kind}?</AlertDialogTitle>
              <AlertDialogDescription>
                The agent won't find it again, and re-ingestion won't bring it back. This can't be
                undone from the app.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  memory.forget(n.node)
                  browse.select(null)
                }}
              >
                Forget
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  )
}

export function AddFact({ memory, browse }: { memory: Memory; browse: Browse }) {
  const [adding, setAdding] = useState(false)
  if (!adding)
    return (
      <Button size="sm" onClick={() => setAdding(true)}>
        <PlusIcon /> Add fact
      </Button>
    )
  return (
    <FactForm
      label="Add fact"
      cancel={() => setAdding(false)}
      submit={(f) => {
        browse.select(memory.add(f))
        setAdding(false)
      }}
    />
  )
}

export function ProfileEditor({ memory, rows = 10 }: { memory: Memory; rows?: number }) {
  const [text, setText] = useState(memory.profile.text)
  const [conflict, setConflict] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => setText(memory.profile.text), [memory.profile.hash]) // eslint-disable-line
  const dirty = text !== memory.profile.text
  return (
    <div className="flex flex-col gap-2">
      {conflict && (
        <Alert variant="destructive">
          <AlertTitle>Changed since you opened it</AlertTitle>
          <AlertDescription>Your edits are still in the editor.</AlertDescription>
          <AlertAction>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                memory.loadTheirs()
                setConflict(false)
              }}
            >
              Load theirs
            </Button>
          </AlertAction>
        </Alert>
      )}
      <Textarea
        className="font-mono"
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setSaved(false)
        }}
      />
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          disabled={!dirty}
          onClick={() => {
            const r = memory.saveProfile(text)
            setConflict(r.kind === "conflict")
            setSaved(r.kind === "ok")
          }}
        >
          Save
        </Button>
        <span className="text-sm text-muted-foreground">
          About {tokensOf(text)} tokens in every prompt
          {saved ? " · saved, applies at the next turn" : ""}
        </span>
      </div>
    </div>
  )
}

export function Stopped() {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>The instance is stopped</EmptyTitle>
        <EmptyDescription>Start it to see its memory.</EmptyDescription>
      </EmptyHeader>
      <Button>Start</Button>
    </Empty>
  )
}

export function StatePanel({ memory }: { memory: Memory }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="fixed right-4 bottom-16 z-40 flex w-96 flex-col gap-2 rounded-lg border bg-background p-3 text-xs shadow-lg">
      <div className="flex items-center justify-between">
        <span className="font-medium">Prototype state</span>
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>
      {open && (
        <>
          <div className="flex flex-wrap gap-1">
            <Button size="xs" variant="outline" onClick={memory.agentRemembers}>
              Agent remembers a fact
            </Button>
            <Button size="xs" variant="outline" onClick={memory.diskEditsProfile}>
              profile.md edited on disk
            </Button>
            <Button
              size="xs"
              variant={memory.running ? "outline" : "destructive"}
              onClick={() => memory.setRunning(!memory.running)}
            >
              Instance: {memory.running ? "running" : "stopped"}
            </Button>
          </div>
          <p className="text-muted-foreground">
            {memory.pending > 0 ? `${memory.pending} agent write not shown until Refresh. ` : ""}
            Try: correct the rebase fact, forget an episode, edit the profile after a disk edit. Red
            calls are missing from the v1 contract.
          </p>
          <ol className="flex max-h-72 flex-col gap-1 overflow-y-auto font-mono">
            {memory.calls.map((c) => (
              <li key={c.id} className={c.missing ? "text-destructive" : ""}>
                {c.method} {c.params} → {c.result}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}
