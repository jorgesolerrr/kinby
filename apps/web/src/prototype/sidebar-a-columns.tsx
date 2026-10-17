// PROTOTYPE sidebar A, "Agents, then threads": Jorge's idea drawn straight. The sidebar holds the
// agents, a second column holds the selected agent's threads with a search and a filter, and the
// agent's own pages sit as icons in that column's header. The Threads button is gone.
import {
  BrainIcon,
  ChartColumnIcon,
  MessageSquarePlusIcon,
  PlusIcon,
  SearchIcon,
  Settings2Icon,
} from "lucide-react"
import { useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

import { instances, threadsByAgent, type ProtoThread } from "./fixtures"
import { AgentAvatar, agentLine, HUB_PAGES, STATUS, ThreadView } from "./parts"

export const name = "Instances, then threads"

const DAYS = ["Today", "Yesterday", "This week", "Earlier"] as const
const AGENT_PAGES = [
  { label: "Memory", icon: BrainIcon },
  { label: "Stats", icon: ChartColumnIcon },
  { label: "Settings", icon: Settings2Icon },
]

type Filter = "all" | "needs" | "routines"

function shown(threads: ProtoThread[], query: string, filter: Filter) {
  const q = query.trim().toLowerCase()
  return threads.filter(
    (thread) =>
      (q === "" || `${thread.title} ${thread.preview}`.toLowerCase().includes(q)) &&
      (filter === "all" ||
        (filter === "needs" &&
          (thread.status === "awaiting_approval" || thread.status === "failed")) ||
        (filter === "routines" && thread.routine !== undefined)),
  )
}

export function SidebarA() {
  const [agentId, setAgentId] = useState("atlas")
  const [threadId, setThreadId] = useState("a1")
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<Filter>("all")
  const agent = instances.find((each) => each.id === agentId) ?? instances[0]
  const all = threadsByAgent[agent.id]
  const threads = shown(all, query, filter)
  const open = all.find((thread) => thread.id === threadId)

  return (
    <div className="flex h-svh">
      <nav
        aria-label="kinby"
        className="flex w-60 shrink-0 flex-col gap-4 border-r bg-sidebar p-2 text-sidebar-foreground"
      >
        <a href="#home" className="flex items-center gap-2 px-2 pt-1">
          <KinbyMark className="size-7" />
          <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
        </a>
        <div className="flex flex-col gap-0.5">
          <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">Instances</p>
          {instances.map((each) => (
            <button
              key={each.id}
              type="button"
              aria-current={each.id === agent.id ? "page" : undefined}
              onClick={() => {
                setAgentId(each.id)
                setThreadId(threadsByAgent[each.id][0]?.id ?? "")
                setQuery("")
              }}
              className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-sidebar-accent"
            >
              <AgentAvatar agent={each} />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium">{each.name}</span>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {agentLine(each)}
                </span>
              </span>
              {each.needsYou > 0 && <span className="size-2 shrink-0 rounded-full bg-ember" />}
            </button>
          ))}
          <a
            href="#new"
            className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent"
          >
            <span className="grid size-8 place-items-center">
              <PlusIcon className="size-4" />
            </span>
            New instance
          </a>
        </div>
        <div className="mt-auto flex flex-col gap-0.5">
          {HUB_PAGES.map((page) => (
            <a
              key={page.label}
              href="#page"
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent"
            >
              <page.icon className="size-4" />
              {page.label}
            </a>
          ))}
        </div>
      </nav>

      <aside
        aria-label={`${agent.name}'s threads`}
        className="flex w-80 shrink-0 flex-col border-r"
      >
        <div className="flex flex-col gap-3 border-b p-3">
          <div className="flex items-center gap-2">
            <AgentAvatar agent={agent} />
            <div className="flex min-w-0 flex-1 flex-col">
              <h2 className="truncate font-heading font-semibold">{agent.name}</h2>
              <p className="truncate text-xs text-muted-foreground">{agent.role}</p>
            </div>
            {AGENT_PAGES.map((page) => (
              <Tooltip key={page.label}>
                <TooltipTrigger
                  render={
                    <Button variant="ghost" size="icon-sm" aria-label={page.label}>
                      <page.icon />
                    </Button>
                  }
                />
                <TooltipContent>{page.label}</TooltipContent>
              </Tooltip>
            ))}
          </div>
          <div className="flex gap-2">
            <InputGroup>
              <InputGroupInput
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
                  <Button size="icon" aria-label="New thread">
                    <MessageSquarePlusIcon />
                  </Button>
                }
              />
              <TooltipContent>New thread</TooltipContent>
            </Tooltip>
          </div>
          <ToggleGroup
            value={[filter]}
            onValueChange={(value) => setFilter((value[0] as Filter | undefined) ?? "all")}
            size="sm"
            variant="outline"
            aria-label="Show"
          >
            <ToggleGroupItem value="all">All</ToggleGroupItem>
            <ToggleGroupItem value="needs">Needs you</ToggleGroupItem>
            <ToggleGroupItem value="routines">Routines</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2">
          {threads.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>{all.length === 0 ? "No threads yet" : "Nothing matches"}</EmptyTitle>
                <EmptyDescription>
                  {all.length === 0
                    ? `Start one and ${agent.name} picks it up.`
                    : "Try other words, or show all threads."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            DAYS.map((day) => {
              const inDay = threads.filter((thread) => thread.day === day)
              if (inDay.length === 0) return null
              return (
                <section key={day} aria-label={day} className="flex flex-col gap-0.5">
                  <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">{day}</h3>
                  {inDay.map((thread) => (
                    <ThreadRow
                      key={thread.id}
                      thread={thread}
                      current={thread.id === threadId}
                      onOpen={() => setThreadId(thread.id)}
                    />
                  ))}
                </section>
              )
            })
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
          <h1 className="truncate text-sm font-semibold">{open?.title ?? "New thread"}</h1>
          {open?.routine && (
            <span className="font-mono text-xs text-muted-foreground">by {open.routine}</span>
          )}
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-24">
          <ThreadView />
        </main>
      </div>
    </div>
  )
}

function ThreadRow({
  thread,
  current,
  onOpen,
}: {
  thread: ProtoThread
  current: boolean
  onOpen: () => void
}) {
  const status = STATUS[thread.status]
  return (
    <button
      type="button"
      aria-current={current ? "page" : undefined}
      onClick={onOpen}
      className="flex flex-col gap-0.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
    >
      <span className="flex items-center gap-2">
        {status !== undefined ? (
          <status.icon
            aria-label={status.label}
            className={`size-3.5 shrink-0 ${status.tone} ${thread.status === "running" ? "animate-spin" : ""}`}
          />
        ) : thread.routine !== undefined ? (
          <span aria-label="Routine" className="grid size-3.5 shrink-0 place-items-center">
            <span className="size-2 rounded-full bg-ember" />
          </span>
        ) : (
          <span aria-hidden className="size-3.5 shrink-0" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{thread.title}</span>
        <span className="font-mono text-xs text-muted-foreground">{thread.when}</span>
      </span>
      <span className="truncate pl-5.5 text-xs text-muted-foreground">{thread.preview}</span>
    </button>
  )
}
