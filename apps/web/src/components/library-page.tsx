import type { Client, Clock, InstanceSummary, LibraryEntry, LibraryKind } from "@kinby/contract"
import {
  ChevronDownIcon,
  ChevronUpIcon,
  FactoryIcon,
  GitBranchIcon,
  type LucideIcon,
  MessageSquareIcon,
  PenLineIcon,
  PlugIcon,
  PlusIcon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { FactoryEntry } from "@/components/library-factory"
import { McpServerEntry, NewMcpServer } from "@/components/library-mcp-server"
import { AddFromGit, SkillEntry, WriteSkill } from "@/components/library-skill"
import { PickedAvatars, UsedBy } from "@/components/library-used-by"
import { PageColumn } from "@/components/page-column"
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
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useRead } from "@/hooks/use-read"
import { instanceName } from "@/lib/instances"
import { type InstancePick, readPicks } from "@/lib/picks"
import { homePath, navigate, openLibraryTab, useLibraryTab } from "@/lib/selection"

type Caller = Pick<Client, "call">

type Form = "write" | "git" | "mcp_server"

const KINDS: {
  kind: LibraryKind
  title: string
  noun: string
  icon: LucideIcon
  none: string
  /** What the kind is for, said when it holds nothing. */
  lead: string
  /** The add action an empty tab offers, when the page can add one. */
  add?: { form: Form; label: string }
}[] = [
  {
    kind: "skill",
    title: "Skills",
    noun: "skill",
    icon: SparklesIcon,
    none: "No skills yet",
    lead: "A skill teaches an instance one kind of task. Write one, or add some from a git repository.",
    add: { form: "write", label: "Write a skill" },
  },
  {
    kind: "mcp_server",
    title: "MCP servers",
    noun: "MCP server",
    icon: PlugIcon,
    none: "No MCP servers yet",
    lead: "An MCP server gives an instance tools from outside kinby, like GitHub or Notion. Add one here, then pick it for an instance.",
    add: { form: "mcp_server", label: "Add MCP server" },
  },
  {
    kind: "factory",
    title: "Factories",
    noun: "factory",
    icon: FactoryIcon,
    none: "No factories yet",
    lead: "A factory runs work through steps across instances. Ask an instance with the write-factory skill to make one.",
  },
]

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
 * or a server opens its form at the top of the list. A tab whose kind holds nothing says what the
 * kind is for, and offers its add action and asking the `home` instance to add one.
 */
export function LibraryPage({
  client,
  clock,
  home,
}: {
  client: Caller
  clock: Clock
  home: InstanceSummary | undefined
}) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  const picks = useCallback(() => readPicks(client), [client])
  const { value: picked, readAgain: readPicksAgain } = useRead(picks, clock)
  // Unknown until the picks are read, and for a factory, whose instances its row reads apart.
  const picksOf = (entry: LibraryEntry) =>
    entry.kind === "factory" ? undefined : picked && (picked[entry.kind].get(entry.name) ?? [])
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
  const matching = listed?.entries.filter((entry) =>
    `${entry.name} ${entry.description ?? ""}`.toLocaleLowerCase().includes(wanted),
  )
  const count = (of: LibraryKind) => matching?.filter((entry) => entry.kind === of).length
  const entries = matching?.filter((entry) => entry.kind === kind)
  const elsewhere = KINDS.filter((each) => each.kind !== kind && (count(each.kind) ?? 0) > 0)
  const heldSkills =
    listed?.entries.filter((entry) => entry.kind === "skill").map((entry) => entry.name) ?? []
  const shownKind = KINDS.find((each) => each.kind === kind)
  const closed = () => {
    show(undefined)
    readAgain()
  }
  const changed = () => {
    readAgain()
    readPicksAgain()
  }
  const deleted = () => {
    show(undefined)
    changed()
  }

  const usedBy = (entry: LibraryEntry) => {
    const picks = picksOf(entry)
    return picks && <UsedBy name={entry.name} picks={picks} />
  }

  const rows = (title: string, listedEntries: LibraryEntry[]) => (
    <ItemGroup variant="outline" aria-label={title}>
      {listedEntries.map((entry) => (
        <EntryRow
          key={entry.name}
          entry={entry}
          noun={shownKind?.noun ?? entry.kind}
          newer={isNewer(entry)}
          picks={picksOf(entry)}
          open={open !== undefined && "entry" in open && open.entry === entry.name}
          onOpenChange={(unfold) => show(unfold ? { entry: entry.name } : undefined)}
        >
          {entry.kind === "skill" ? (
            <SkillEntry
              client={client}
              clock={clock}
              entry={entry}
              newer={isNewer(entry)}
              usedBy={usedBy(entry)}
              onSaved={changed}
              onChecked={(newer) => marked(entry.name, newer)}
              onMoved={() => {
                marked(entry.name, false)
                changed()
              }}
              onDeleted={deleted}
            />
          ) : entry.kind === "mcp_server" ? (
            <>
              {usedBy(entry)}
              <McpServerEntry
                client={client}
                clock={clock}
                entry={entry}
                onSaved={changed}
                onDeleted={deleted}
              />
            </>
          ) : (
            <FactoryEntry client={client} clock={clock} name={entry.name} />
          )}
        </EntryRow>
      ))}
    </ItemGroup>
  )

  return (
    <Tabs value={kind} onValueChange={(picked: LibraryKind) => openLibraryTab(picked)}>
      <header className="sticky top-0 z-10 border-b bg-background">
        <PageColumn className="flex flex-col gap-4 pt-6">
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
                <span className="font-mono text-xs text-muted-foreground">{count(kind)}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </PageColumn>
      </header>
      <TabsContent value={kind}>
        <PageColumn className="flex flex-col gap-8 py-6 text-base">
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
          ) : entries.length === 0 && wanted === "" && shownKind !== undefined ? (
            <EmptyKind kind={shownKind} home={home} onAdd={(form) => show({ form })} />
          ) : entries.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>Nothing here matches &quot;{query.trim()}&quot;</EmptyTitle>
                {elsewhere.length > 0 && (
                  <EmptyDescription>
                    {elsewhere.map((each) => (
                      <Button
                        key={each.kind}
                        variant="link"
                        size="sm"
                        onClick={() => openLibraryTab(each.kind)}
                      >
                        {count(each.kind)} in {each.title}
                      </Button>
                    ))}
                  </EmptyDescription>
                )}
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
        </PageColumn>
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

/** A kind that holds nothing: what it is for, its add action, and asking `home` to add one. */
function EmptyKind({
  kind: { icon: Icon, none, lead, add },
  home,
  onAdd,
}: {
  kind: (typeof KINDS)[number]
  home: InstanceSummary | undefined
  onAdd: (form: Form) => void
}) {
  return (
    <Empty variant="outline">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle>{none}</EmptyTitle>
        <EmptyDescription>{lead}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="flex-row flex-wrap justify-center">
        {add !== undefined && (
          <Button onClick={() => onAdd(add.form)}>
            <PlusIcon data-icon="inline-start" />
            {add.label}
          </Button>
        )}
        {home !== undefined && (
          <Button variant="outline" onClick={() => navigate(homePath(home.instance_id))}>
            <MessageSquareIcon data-icon="inline-start" />
            Ask {instanceName(home)} to add one
          </Button>
        )}
      </EmptyContent>
    </Empty>
  )
}

/** The open tab's one action: New skill's two ways, Add MCP server, or how a factory is made. */
function TabAction({ kind, onAdd }: { kind: LibraryKind; onAdd: (form: Form) => void }) {
  if (kind === "factory") {
    return (
      <p className="w-full text-xs text-muted-foreground md:w-auto md:max-w-56 md:text-right">
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
 * One entry's row: its name, an Edited or Git badge, a dot for a newer version, its description,
 * and the instances that picked it. Clicking it unfolds `children` under it.
 */
function EntryRow({
  entry,
  noun,
  newer,
  picks,
  open,
  onOpenChange,
  children,
}: {
  entry: LibraryEntry
  /** What the entry's kind is called. */
  noun: string
  newer: boolean
  picks: InstancePick[] | undefined
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} render={<li aria-label={entry.name} />}>
      <CollapsibleTrigger
        render={
          <Item
            size="sm"
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
            {newer && <span aria-hidden className="size-2 shrink-0 rounded-full bg-ember" />}
            {entry.edited && <Badge variant="outline">Edited</Badge>}
            {entry.source === "git" && <Badge variant="secondary">Git</Badge>}
          </ItemTitle>
          {entry.description !== null && (
            <ItemDescription lines="one">{entry.description}</ItemDescription>
          )}
        </ItemContent>
        <ItemActions>
          {picks && <PickedAvatars picks={picks} />}
          {open ? (
            <ChevronUpIcon className="text-muted-foreground" />
          ) : (
            <ChevronDownIcon className="text-muted-foreground" />
          )}
        </ItemActions>
      </CollapsibleTrigger>
      <CollapsibleContent variant="muted">
        <div className="flex flex-col gap-4 text-sm">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}
