// PROTOTYPE · Variant A, "Tabs + rows". The instance page's grammar, carried onto the Library: a
// header that stays put with the title, one search and the tab's one action; line tabs per kind
// with counts; each tab one flat list of rows that unfold in place into the entry's detail (who
// uses it, its content, its actions). Adding opens in place at the top of the list.
import { ChevronDownIcon, GitBranchIcon, PenLineIcon, PlusIcon, SearchIcon } from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "cn"

import type { Go } from "../shell/screens"
import { type Entry, type Factory, KINDS, type Kind, byKind, matches } from "./mock"
import {
  AddFromGit,
  FactoryInstances,
  FactoryMakeup,
  GitVersion,
  McpFields,
  NewMcpServer,
  NewSkill,
  SkillEditor,
  UsedBy,
  UsedStack,
  origin,
  usedLine,
} from "./shared"

type Adding = "write" | "git" | "mcp" | undefined

export function LibraryA({ go, kind, onKind }: { go: Go; kind: Kind; onKind: (k: Kind) => void }) {
  const [query, setQuery] = useState("")
  const [adding, setAdding] = useState<Adding>()
  const [open, setOpen] = useState<string | undefined>(kind === "factory" ? "software" : undefined)
  const counts = Object.fromEntries(
    KINDS.map((k) => [k.kind, byKind(k.kind).filter((e) => matches(e, query)).length]),
  ) as Record<Kind, number>
  const searching = query.trim() !== ""

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Tabs
        value={kind}
        onValueChange={(v: Kind) => {
          onKind(v)
          setAdding(undefined)
        }}
        className="gap-0"
      >
        <div className="sticky top-0 z-10 bg-background">
          <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 pt-6 md:px-8">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex min-w-0 flex-1 flex-col">
                <h1 className="font-heading text-xl font-semibold">Library</h1>
                <p className="text-sm text-muted-foreground">
                  What your instances can pick. Nothing here reaches one until it's picked.
                </p>
              </div>
              <AddAction kind={kind} onAdd={setAdding} />
            </div>
            <InputGroup>
              <InputGroupInput
                type="search"
                placeholder="Search skills, MCP servers and factories"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <InputGroupAddon>
                <SearchIcon />
              </InputGroupAddon>
            </InputGroup>
            <TabsList variant="line" className="w-full justify-start overflow-x-auto">
              {KINDS.map((k) => (
                <TabsTrigger key={k.kind} value={k.kind} className="flex-none">
                  {k.title}
                  <span
                    className={cn(
                      "font-mono text-xs text-muted-foreground",
                      searching && counts[k.kind] > 0 && "text-foreground",
                    )}
                  >
                    {counts[k.kind]}
                  </span>
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <Separator />
        </div>
        <div className="mx-auto w-full max-w-4xl px-4 py-6 md:px-8">
          {KINDS.map((k) => (
            <TabsContent key={k.kind} value={k.kind} className="flex flex-col gap-8">
              {adding && <AddPanel adding={adding} onDone={() => setAdding(undefined)} />}
              <KindList
                kind={k.kind}
                query={query}
                counts={counts}
                open={open}
                onOpen={setOpen}
                onKind={onKind}
                go={go}
              />
            </TabsContent>
          ))}
        </div>
      </Tabs>
    </div>
  )
}

/** The header's one action: it follows the tab. */
function AddAction({ kind, onAdd }: { kind: Kind; onAdd: (a: Adding) => void }) {
  if (kind === "mcp_server") {
    return (
      <Button size="sm" onClick={() => onAdd("mcp")}>
        <PlusIcon data-icon="inline-start" />
        Add MCP server
      </Button>
    )
  }
  if (kind === "factory") {
    return (
      <p className="max-w-56 text-right text-xs text-muted-foreground">
        Ask an instance with the write-factory skill to make one.
      </p>
    )
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button size="sm" />}>
        <PlusIcon data-icon="inline-start" />
        New skill
        <ChevronDownIcon data-icon="inline-end" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => onAdd("write")}>
            <PenLineIcon />
            Write a skill
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onAdd("git")}>
            <GitBranchIcon />
            Add from git
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function AddPanel({ adding, onDone }: { adding: NonNullable<Adding>; onDone: () => void }) {
  const title =
    adding === "write"
      ? "Write a skill"
      : adding === "git"
        ? "Add skills from git"
        : "Add an MCP server"
  return (
    <section className="flex flex-col gap-4 rounded-xl border bg-muted/40 p-4">
      <h2 className="font-heading text-base font-semibold">{title}</h2>
      {adding === "write" ? (
        <NewSkill onDone={onDone} />
      ) : adding === "git" ? (
        <AddFromGit onDone={onDone} />
      ) : (
        <NewMcpServer onDone={onDone} />
      )}
    </section>
  )
}

function KindList({
  kind,
  query,
  counts,
  open,
  onOpen,
  onKind,
  go,
}: {
  kind: Kind
  query: string
  counts: Record<Kind, number>
  open: string | undefined
  onOpen: (name: string | undefined) => void
  onKind: (k: Kind) => void
  go: Go
}) {
  const shown = byKind(kind).filter((e) => matches(e, query))
  if (shown.length === 0) {
    const elsewhere = KINDS.filter((k) => k.kind !== kind && counts[k.kind] > 0)
    return (
      <Empty className="border">
        <EmptyHeader>
          <EmptyTitle>Nothing here matches "{query.trim()}"</EmptyTitle>
          {elsewhere.length > 0 && (
            <EmptyDescription>
              {elsewhere.map((k, n) => (
                <span key={k.kind}>
                  {n > 0 && ", "}
                  <button type="button" className="underline" onClick={() => onKind(k.kind)}>
                    {counts[k.kind]} in {k.title}
                  </button>
                </span>
              ))}
            </EmptyDescription>
          )}
        </EmptyHeader>
      </Empty>
    )
  }
  if (kind === "skill") {
    const yours = shown.filter((e) => e.source !== "shipped")
    const shipped = shown.filter((e) => e.source === "shipped")
    return (
      <>
        {yours.length > 0 && (
          <Section title="Yours" lead="Skills you wrote or added from git">
            <Rows entries={yours} open={open} onOpen={onOpen} go={go} />
          </Section>
        )}
        {shipped.length > 0 && (
          <Section
            title="Shipped with kinby"
            lead="Edit one to make it yours; updates keep your edit"
          >
            <Rows entries={shipped} open={open} onOpen={onOpen} go={go} />
          </Section>
        )}
      </>
    )
  }
  return (
    <Section
      title={kind === "factory" ? "Factories" : "MCP servers"}
      lead={
        kind === "factory"
          ? "Teams of instances that run work through steps"
          : "Tools an instance reaches over MCP once it picks the server"
      }
    >
      <Rows entries={shown} open={open} onOpen={onOpen} go={go} />
    </Section>
  )
}

function Section({
  title,
  lead,
  children,
}: {
  title: string
  lead?: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-base font-semibold">{title}</h2>
        {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
      </div>
      {children}
    </section>
  )
}

function Rows({
  entries,
  open,
  onOpen,
  go,
}: {
  entries: Entry[]
  open: string | undefined
  onOpen: (name: string | undefined) => void
  go: Go
}) {
  return (
    <ItemGroup className="gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none">
      {entries.map((e) => (
        <Collapsible
          key={e.name}
          open={open === e.name}
          onOpenChange={(o) => onOpen(o ? e.name : undefined)}
        >
          <CollapsibleTrigger
            render={
              <Item
                size="sm"
                className="w-full cursor-pointer text-left hover:bg-muted/50"
                render={<button type="button" />}
              />
            }
          >
            <ItemContent className="min-w-0">
              <ItemTitle className="w-full">
                <span className="truncate font-mono">{e.name}</span>
                {e.newer && (
                  <span
                    aria-label="Newer version"
                    className="size-2 shrink-0 rounded-full bg-ember"
                  />
                )}
                {e.edited && <Badge variant="outline">Edited</Badge>}
                {e.source === "git" && <Badge variant="secondary">Git</Badge>}
              </ItemTitle>
              <ItemDescription className="line-clamp-1">{e.description}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <span className="hidden text-xs text-muted-foreground sm:inline">
                {e.usedBy.length === 0 ? "Not picked" : ""}
              </span>
              <UsedStack entry={e} />
              <ChevronDownIcon
                className={cn(
                  "size-4 text-muted-foreground transition-transform",
                  open === e.name && "rotate-180",
                )}
              />
            </ItemActions>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="flex flex-col gap-5 border-t bg-muted/40 p-4">
              <Detail entry={e} go={go} />
            </div>
          </CollapsibleContent>
        </Collapsible>
      ))}
    </ItemGroup>
  )
}

function Detail({ entry, go }: { entry: Entry; go: Go }) {
  if (entry.kind === "factory") {
    const f = entry as Factory
    return (
      <>
        <Sub title="Instances and runs">
          <FactoryInstances factory={f} go={go} />
        </Sub>
        <Sub title="What it's made of">
          <FactoryMakeup factory={f} />
        </Sub>
      </>
    )
  }
  return (
    <>
      <GitVersion entry={entry} />
      <Sub title={usedLine(entry)}>
        <UsedBy entry={entry} go={go} />
      </Sub>
      {entry.kind === "skill" ? <SkillEditor entry={entry} /> : <McpFields entry={entry} />}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{origin(entry)}</span>
        <Button size="sm" variant="ghost" className="ml-auto">
          Delete
        </Button>
        <Button size="sm">Save</Button>
      </div>
    </>
  )
}

function Sub({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </div>
  )
}
