// PROTOTYPE · Variant B, "List + detail". Two panes, like a mail client or Claude's own skill
// settings: on the left, one search over one list with every kind grouped under its header and a
// single Add menu; on the right, the open entry as a page of its own (header with its actions,
// then who uses it, then its content). Adding opens its form in the right pane. On a phone the
// list fills the screen and an entry drills in with Back.
import {
  ArrowLeftIcon,
  BlocksIcon,
  FactoryIcon,
  GitBranchIcon,
  LibraryIcon,
  MoreHorizontalIcon,
  PenLineIcon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
} from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { cn } from "cn"

import type { Go } from "../shell/screens"
import { ALL, type Entry, type Factory, KINDS, type Kind, SOURCES, matches } from "./mock"
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

type Open = { entry: Entry } | { adding: "write" | "git" | "mcp" } | undefined

const ICON: Record<Kind, React.ReactNode> = {
  skill: <BlocksIcon />,
  mcp_server: <ServerIcon />,
  factory: <FactoryIcon />,
}

export function LibraryB({ go, kind }: { go: Go; kind: Kind }) {
  const [query, setQuery] = useState("")
  const [open, setOpen] = useState<Open>(() => ({
    entry: ALL.find((e) => e.kind === kind) ?? ALL[0],
  }))
  const shown = ALL.filter((e) => matches(e, query))

  return (
    <div className="flex min-h-0 flex-1">
      <section
        aria-label="Library"
        className={cn(
          "flex min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r",
          open !== undefined && "hidden md:flex",
        )}
      >
        <div className="flex flex-col gap-3 p-4">
          <div className="flex items-center gap-2">
            <h1 className="flex-1 font-heading text-xl font-semibold">Library</h1>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button size="icon-sm" variant="outline" aria-label="Add" />}
              >
                <PlusIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuItem onClick={() => setOpen({ adding: "write" })}>
                    <PenLineIcon />
                    Write a skill
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setOpen({ adding: "git" })}>
                    <GitBranchIcon />
                    Add skills from git
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setOpen({ adding: "mcp" })}>
                    <ServerIcon />
                    Add an MCP server
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <InputGroup>
            <InputGroupInput
              type="search"
              placeholder="Search the library"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
          </InputGroup>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-2 pb-4">
          {shown.length === 0 && (
            <p className="px-3 text-sm text-muted-foreground">Nothing matches "{query.trim()}"</p>
          )}
          {KINDS.map((k) => {
            const entries = shown.filter((e) => e.kind === k.kind)
            if (entries.length === 0) return null
            return (
              <div key={k.kind} className="flex flex-col gap-1">
                <span className="flex items-center gap-2 px-3 text-xs font-medium text-muted-foreground">
                  {k.title}
                  <span className="font-mono">{entries.length}</span>
                </span>
                <ItemGroup className="gap-0.5">
                  {entries.map((e) => {
                    const selected =
                      open !== undefined && "entry" in open && open.entry.name === e.name
                    return (
                      <Item
                        key={e.name}
                        size="xs"
                        variant={selected ? "muted" : "default"}
                        className="text-left hover:bg-muted/50"
                        render={<button type="button" onClick={() => setOpen({ entry: e })} />}
                      >
                        <ItemMedia variant="icon" className="text-muted-foreground">
                          {ICON[e.kind]}
                        </ItemMedia>
                        <ItemContent className="min-w-0">
                          <ItemTitle className="w-full">
                            <span className="min-w-0 flex-1 truncate font-mono">{e.name}</span>
                            {e.newer && (
                              <span
                                aria-label="Newer version"
                                className="size-2 shrink-0 rounded-full bg-ember"
                              />
                            )}
                            <span className="shrink-0 text-xs font-normal text-muted-foreground">
                              {e.usedBy.length > 0 ? e.usedBy.length : "–"}
                            </span>
                          </ItemTitle>
                        </ItemContent>
                      </Item>
                    )
                  })}
                </ItemGroup>
              </div>
            )
          })}
        </div>
      </section>
      <section
        className={cn("min-w-0 flex-1 overflow-y-auto", open === undefined && "hidden md:block")}
      >
        {open === undefined ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <LibraryIcon />
              </EmptyMedia>
              <EmptyTitle>Nothing open</EmptyTitle>
              <EmptyDescription>Pick a skill, an MCP server or a factory.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : "adding" in open ? (
          <Page
            title={
              open.adding === "write"
                ? "Write a skill"
                : open.adding === "git"
                  ? "Add skills from git"
                  : "Add an MCP server"
            }
            onBack={() => setOpen(undefined)}
          >
            {open.adding === "write" ? (
              <NewSkill onDone={() => setOpen(undefined)} />
            ) : open.adding === "git" ? (
              <AddFromGit onDone={() => setOpen(undefined)} />
            ) : (
              <NewMcpServer onDone={() => setOpen(undefined)} />
            )}
          </Page>
        ) : (
          <EntryPage
            key={open.entry.name}
            entry={open.entry}
            go={go}
            onBack={() => setOpen(undefined)}
          />
        )}
      </section>
    </div>
  )
}

function Page({
  title,
  lead,
  badges,
  actions,
  onBack,
  children,
}: {
  title: string
  lead?: string
  badges?: React.ReactNode
  actions?: React.ReactNode
  onBack: () => void
  children: React.ReactNode
}) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6 md:px-8">
      <header className="flex flex-wrap items-start gap-3">
        <Button
          size="icon-sm"
          variant="ghost"
          className="md:hidden"
          aria-label="Back"
          onClick={onBack}
        >
          <ArrowLeftIcon />
        </Button>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h2 className="flex flex-wrap items-center gap-2 font-mono text-xl font-semibold">
            {title}
            {badges}
          </h2>
          {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
        </div>
        {actions}
      </header>
      {children}
    </div>
  )
}

function EntryPage({ entry, go, onBack }: { entry: Entry; go: Go; onBack: () => void }) {
  const factory = entry.kind === "factory" ? (entry as Factory) : undefined
  return (
    <Page
      title={entry.name}
      lead={entry.description}
      onBack={onBack}
      badges={
        <>
          <Badge variant="secondary">{SOURCES[entry.source]}</Badge>
          {entry.edited && <Badge variant="outline">Edited</Badge>}
        </>
      }
      actions={
        factory ? undefined : (
          <div className="flex items-center gap-1">
            <Button size="sm">Save</Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button size="icon-sm" variant="ghost" aria-label="More" />}
              >
                <MoreHorizontalIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuItem variant="destructive">Delete {entry.name}…</DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      }
    >
      {factory ? (
        <>
          <Block title="Instances and runs">
            <FactoryInstances factory={factory} go={go} />
          </Block>
          <Block title="What it's made of">
            <FactoryMakeup factory={factory} />
          </Block>
        </>
      ) : (
        <>
          <GitVersion entry={entry} />
          <Block title={usedLine(entry)} aside={<UsedStack entry={entry} />}>
            <UsedBy entry={entry} go={go} />
          </Block>
          <Block
            title={entry.kind === "skill" ? "Content" : "Definition"}
            aside={<span className="text-xs text-muted-foreground">{origin(entry)}</span>}
          >
            {entry.kind === "skill" ? (
              <SkillEditor entry={entry} rows={18} />
            ) : (
              <McpFields entry={entry} />
            )}
          </Block>
        </>
      )}
    </Page>
  )
}

function Block({
  title,
  aside,
  children,
}: {
  title: string
  aside?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h3 className="flex-1 font-heading text-base font-semibold">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}
