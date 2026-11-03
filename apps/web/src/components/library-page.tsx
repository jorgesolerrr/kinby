import type { Client, Clock, LibraryEntry, LibraryKind } from "@kinby/contract"
import {
  ChevronDownIcon,
  ChevronUpIcon,
  GitBranchIcon,
  PenLineIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { FactoryEntry } from "@/components/library-factory"
import { McpServerEntry, NewMcpServer } from "@/components/library-mcp-server"
import { AddFromGit, SkillEntry, WriteSkill } from "@/components/library-skill"
import { SectionFrame } from "@/components/section-frame"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useRead } from "@/hooks/use-read"
import { openLibraryTab, useLibraryTab } from "@/lib/selection"

type Caller = Pick<Client, "call">

const KINDS: { kind: LibraryKind; title: string; noun: string; none: string }[] = [
  { kind: "skill", title: "Skills", noun: "skill", none: "No skills yet" },
  { kind: "mcp_server", title: "MCP servers", noun: "MCP server", none: "No MCP servers yet" },
  { kind: "factory", title: "Factories", noun: "factory", none: "No factories yet" },
]

type Form = "write" | "git" | "mcp_server"

const FORMS: Record<Form, string> = {
  write: "Write a skill",
  git: "Add from git",
  mcp_server: "Add an MCP server",
}

/** What the open tab has open: one of its add forms, or one entry unfolded. One at a time. */
type Open = { form: Form } | { entry: string }

/** A form's first field takes the focus when the form opens. */
const focusFirstField = (panel: HTMLElement | null) =>
  panel?.querySelector<HTMLElement>("input, textarea")?.focus()

/**
 * The hub's library: a header that stays put, with the search and the open tab's action, over a
 * tab per kind, at its own path. Each tab lists its entries as rows under section headers, and a
 * row unfolds in place to read the entry, and a skill or a server to edit or delete. Adding a skill
 * or a server opens its form at the top of the list.
 */
export function LibraryPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  const kind = useLibraryTab() ?? "skill"
  const [query, setQuery] = useState("")
  // Kept with the tab it opened on, so Back to another tab opens nothing.
  const [opened, setOpened] = useState<{ kind: LibraryKind; open: Open }>()
  const open = opened?.kind === kind ? opened.open : undefined
  const show = (next?: Open) => setOpened(next && { kind, open: next })
  // A git skill's newer commit is known only from a check, which the list does not run.
  const [checkedNewer, setCheckedNewer] = useState<string[]>([])
  const marked = (name: string, newer: boolean) =>
    setCheckedNewer((names) => [...names.filter((each) => each !== name), ...(newer ? [name] : [])])
  const isNewer = (entry: LibraryEntry) => entry.newer === true || checkedNewer.includes(entry.name)
  const wanted = query.trim().toLocaleLowerCase()
  const entries = listed?.entries.filter(
    (entry) =>
      entry.kind === kind &&
      `${entry.name} ${entry.description ?? ""}`.toLocaleLowerCase().includes(wanted),
  )
  const heldSkills =
    listed?.entries.filter((entry) => entry.kind === "skill").map((entry) => entry.name) ?? []
  const shownKind = KINDS.find((each) => each.kind === kind)
  const closed = () => {
    show(undefined)
    readAgain()
  }

  const rows = (title: string, listedEntries: LibraryEntry[]) => (
    <ul aria-label={title} className="flex flex-col divide-y">
      {listedEntries.map((entry) => (
        <EntryRow
          key={entry.name}
          entry={entry}
          noun={shownKind?.noun ?? entry.kind}
          newer={isNewer(entry)}
          open={open !== undefined && "entry" in open && open.entry === entry.name}
          onOpenChange={(unfold) => show(unfold ? { entry: entry.name } : undefined)}
        >
          {entry.kind === "skill" ? (
            <SkillEntry
              client={client}
              clock={clock}
              entry={entry}
              newer={isNewer(entry)}
              onSaved={readAgain}
              onChecked={(newer) => marked(entry.name, newer)}
              onMoved={() => {
                marked(entry.name, false)
                readAgain()
              }}
              onDeleted={closed}
            />
          ) : entry.kind === "mcp_server" ? (
            <McpServerEntry
              client={client}
              clock={clock}
              entry={entry}
              onSaved={readAgain}
              onDeleted={closed}
            />
          ) : (
            <FactoryEntry client={client} clock={clock} name={entry.name} />
          )}
        </EntryRow>
      ))}
    </ul>
  )

  return (
    <Tabs value={kind} onValueChange={(picked: LibraryKind) => openLibraryTab(picked)}>
      <header className="sticky top-0 z-10 border-b bg-background">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 pt-6 md:px-8">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <h1 className="font-heading text-xl font-semibold">Library</h1>
              <p className="text-sm text-muted-foreground">
                What your instances can pick. Nothing here reaches one until it&apos;s picked.
              </p>
            </div>
            <TabAction kind={kind} onAdd={(form) => show({ form })} />
          </div>
          <InputGroup>
            <InputGroupInput
              type="search"
              aria-label="Search the library"
              placeholder="Search the library"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
          </InputGroup>
          <TabsList variant="line" className="w-full justify-start overflow-x-auto">
            {KINDS.map(({ kind, title }) => (
              <TabsTrigger key={kind} value={kind} className="flex-none">
                {title}{" "}
                <span className="font-mono text-xs text-muted-foreground">
                  {listed?.entries.filter((entry) => entry.kind === kind).length}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </header>
      <TabsContent value={kind}>
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 py-6 text-base md:px-8">
          {open !== undefined && "form" in open && (
            <AddPanel title={FORMS[open.form]}>
              {open.form === "write" ? (
                <WriteSkill client={client} onWritten={closed} onCancel={() => show(undefined)} />
              ) : open.form === "git" ? (
                <AddFromGit
                  client={client}
                  held={heldSkills}
                  onAdded={closed}
                  onCancel={() => show(undefined)}
                />
              ) : (
                <NewMcpServer client={client} onAdded={closed} onCancel={() => show(undefined)} />
              )}
            </AddPanel>
          )}
          {failure !== undefined && <Failure error={failure} />}
          {entries === undefined ? (
            failure === undefined && <Skeleton className="h-72 w-full" />
          ) : entries.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>
                  {wanted === "" ? shownKind?.none : `Nothing matches "${query.trim()}"`}
                </EmptyTitle>
              </EmptyHeader>
            </Empty>
          ) : kind === "skill" ? (
            <SkillSections entries={entries} rows={rows} />
          ) : kind === "mcp_server" ? (
            <SectionFrame
              variant="page"
              title="MCP servers"
              lead="Outside servers whose tools an instance gets once it picks one."
            >
              {rows("MCP servers", entries)}
            </SectionFrame>
          ) : (
            <SectionFrame
              variant="page"
              title="Factories"
              lead="Steps the hub runs to carry a work item between the instances that hold its roles."
            >
              {rows("Factories", entries)}
            </SectionFrame>
          )}
        </div>
      </TabsContent>
    </Tabs>
  )
}

/** The user's skills, written or added from git, apart from those kinby ships. */
function SkillSections({
  entries,
  rows,
}: {
  entries: LibraryEntry[]
  rows: (title: string, entries: LibraryEntry[]) => ReactNode
}) {
  const yours = entries.filter((entry) => entry.source !== "shipped")
  const shipped = entries.filter((entry) => entry.source === "shipped")
  return (
    <>
      {yours.length > 0 && (
        <SectionFrame variant="page" title="Yours" lead="Skills you wrote or added from git.">
          {rows("Yours", yours)}
        </SectionFrame>
      )}
      {shipped.length > 0 && (
        <SectionFrame variant="page" title="Shipped with kinby" lead="Skills that come with kinby.">
          {rows("Shipped with kinby", shipped)}
        </SectionFrame>
      )}
    </>
  )
}

/** The open tab's one action: New skill's two ways, Add MCP server, or how a factory is made. */
function TabAction({ kind, onAdd }: { kind: LibraryKind; onAdd: (form: Form) => void }) {
  if (kind === "factory") {
    return (
      <p className="text-sm text-muted-foreground">
        An instance with the write-factory skill makes or changes a factory.
      </p>
    )
  }
  if (kind === "mcp_server") {
    return (
      <Button size="sm" onClick={() => onAdd("mcp_server")}>
        <PlusIcon data-icon="inline-start" />
        Add MCP server
      </Button>
    )
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button size="sm" />}>
        <PlusIcon data-icon="inline-start" />
        New skill
        <ChevronDownIcon data-icon="inline-end" />
      </DropdownMenuTrigger>
      {/* The form that opens takes the focus, so closing the menu leaves it there. */}
      <DropdownMenuContent align="end" finalFocus={false}>
        <DropdownMenuItem onClick={() => onAdd("write")}>
          <PenLineIcon />
          Write a skill
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onAdd("git")}>
          <GitBranchIcon />
          Add from git
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** An add form at the top of the list, focused on its first field. */
function AddPanel({ title, children }: { title: string; children: ReactNode }) {
  const titleId = useId()
  return (
    <section
      ref={focusFirstField}
      aria-labelledby={titleId}
      className="flex flex-col gap-4 rounded-xl border bg-muted/30 p-4"
    >
      <h2 id={titleId} className="font-heading text-base font-semibold">
        {title}
      </h2>
      {children}
    </section>
  )
}

/**
 * One entry's row: its name, an Edited or Git badge, a dot for a newer version, and its
 * description. Clicking it unfolds `children` under it.
 */
function EntryRow({
  entry,
  noun,
  newer,
  open,
  onOpenChange,
  children,
}: {
  entry: LibraryEntry
  /** What the entry's kind is called. */
  noun: string
  newer: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} render={<li aria-label={entry.name} />}>
      <CollapsibleTrigger
        render={
          <Item
            size="row"
            render={
              <button
                type="button"
                aria-label={`${entry.name}, ${entry.source} ${noun}${entry.edited ? ", edited" : ""}${newer ? ", newer" : ""}`}
              />
            }
          />
        }
      >
        <ItemContent className="min-w-0">
          <ItemTitle>
            <span className="truncate font-mono">{entry.name}</span>
            {newer && (
              <span aria-hidden className="size-2 shrink-0 rounded-full bg-muted-foreground" />
            )}
            {entry.edited && <Badge variant="outline">Edited</Badge>}
            {entry.source === "git" && <Badge variant="secondary">Git</Badge>}
          </ItemTitle>
          {entry.description !== null && (
            <ItemDescription lines="one">{entry.description}</ItemDescription>
          )}
        </ItemContent>
        <ItemActions>
          {open ? (
            <ChevronUpIcon className="text-muted-foreground" />
          ) : (
            <ChevronDownIcon className="text-muted-foreground" />
          )}
        </ItemActions>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex flex-col gap-4 pb-4 text-sm">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}
