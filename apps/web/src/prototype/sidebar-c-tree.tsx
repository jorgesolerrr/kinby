// PROTOTYPE sidebar C, "Agent tree": the sidebar holds the agents, and the selected one opens its
// own pages under it, the way a file tree opens a folder. The threads column is only threads:
// New thread on top, a search that can reach every agent, and fuller rows with two lines of what
// was said, grouped by what they need from the user.
import {
  BrainIcon,
  ChartColumnIcon,
  HomeIcon,
  MessagesSquareIcon,
  PlusIcon,
  SearchIcon,
  Settings2Icon,
} from "lucide-react"
import { useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import { instances, threadsByAgent, type ProtoThread } from "./fixtures"
import { AgentAvatar, HUB_PAGES, STATUS, ThreadView } from "./parts"

export const name = "Instance tree"

const PAGES = [
  { label: "Threads", icon: MessagesSquareIcon },
  { label: "Overview", icon: HomeIcon },
  { label: "Memory", icon: BrainIcon },
  { label: "Stats", icon: ChartColumnIcon },
  { label: "Settings", icon: Settings2Icon },
]

const GROUPS: { label: string; has: (thread: ProtoThread) => boolean }[] = [
  { label: "Needs you", has: (t) => t.status === "awaiting_approval" || t.status === "failed" },
  { label: "Working", has: (t) => t.status === "running" },
  { label: "Done", has: (t) => t.status === "idle" },
]

export function SidebarC() {
  const [agentId, setAgentId] = useState("atlas")
  const [threadId, setThreadId] = useState("a1")
  const [query, setQuery] = useState("")
  const [scope, setScope] = useState<"agent" | "all">("agent")
  const agent = instances.find((each) => each.id === agentId) ?? instances[0]
  const q = query.trim().toLowerCase()
  const pool = (scope === "all" && q !== "" ? instances : [agent]).flatMap((each) =>
    threadsByAgent[each.id].map((thread) => ({ thread, agent: each })),
  )
  const threads = pool.filter(
    ({ thread }) => q === "" || `${thread.title} ${thread.preview}`.toLowerCase().includes(q),
  )
  const open = pool.find(({ thread }) => thread.id === threadId)?.thread

  return (
    <div className="flex h-svh">
      <nav
        aria-label="kinby"
        className="flex w-56 shrink-0 flex-col gap-4 border-r bg-sidebar p-2 text-sidebar-foreground"
      >
        <a href="#home" className="flex items-center gap-2 px-2 pt-1">
          <KinbyMark className="size-7" />
          <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
        </a>
        <ul aria-label="Instances" className="flex flex-col gap-0.5">
          {instances.map((each) => {
            const selected = each.id === agent.id
            return (
              <li key={each.id} className="flex flex-col">
                <button
                  type="button"
                  aria-expanded={selected}
                  onClick={() => {
                    setAgentId(each.id)
                    setThreadId(threadsByAgent[each.id][0]?.id ?? "")
                  }}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <AgentAvatar agent={each} size="sm" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{each.name}</span>
                  {each.needsYou > 0 && (
                    <Badge variant="attention" aria-label={`${each.needsYou} waiting for you`}>
                      {each.needsYou}
                    </Badge>
                  )}
                </button>
                {selected && (
                  <ul className="ml-4 flex flex-col gap-0.5 border-l py-1 pl-2">
                    {PAGES.map((page) => (
                      <li key={page.label}>
                        <a
                          href="#page"
                          aria-current={page.label === "Threads" ? "page" : undefined}
                          className="flex items-center gap-2 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-sidebar-accent hover:text-foreground aria-[current=page]:bg-sidebar-accent aria-[current=page]:text-foreground"
                        >
                          <page.icon className="size-4" />
                          {page.label}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
          <li>
            <a
              href="#new"
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent"
            >
              <PlusIcon className="size-4" />
              New instance
            </a>
          </li>
        </ul>
        <div className="mt-auto flex flex-col gap-0.5 border-t pt-2">
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

      <aside aria-label="Threads" className="flex w-80 shrink-0 flex-col border-r">
        <div className="flex flex-col gap-2 p-3">
          <Button className="w-full">
            <PlusIcon data-icon="inline-start" />
            New thread with {agent.name}
          </Button>
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
          {q !== "" && (
            <ToggleGroup
              value={[scope]}
              onValueChange={(value) =>
                setScope((value[0] as "agent" | "all" | undefined) ?? "agent")
              }
              size="sm"
              variant="outline"
              aria-label="Search in"
            >
              <ToggleGroupItem value="agent">{agent.name}</ToggleGroupItem>
              <ToggleGroupItem value="all">All instances</ToggleGroupItem>
            </ToggleGroup>
          )}
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-2 pb-4">
          {GROUPS.map((group) => {
            const inGroup = threads.filter(({ thread }) => group.has(thread))
            if (inGroup.length === 0) return null
            return (
              <section key={group.label} aria-label={group.label} className="flex flex-col gap-1">
                <h3 className="flex items-center justify-between px-2 text-xs font-medium text-muted-foreground">
                  {group.label}
                  <span className="font-mono">{inGroup.length}</span>
                </h3>
                {inGroup.map(({ thread, agent: owner }) => (
                  <Card
                    key={thread.id}
                    thread={thread}
                    owner={scope === "all" && q !== "" ? owner.name : undefined}
                    current={thread.id === threadId}
                    onOpen={() => setThreadId(thread.id)}
                  />
                ))}
              </section>
            )
          })}
          {threads.length === 0 && (
            <p className="px-2 text-sm text-muted-foreground">
              {q === "" ? `${agent.name} has no threads yet.` : `No thread matches “${query}”.`}
            </p>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
          <h1 className="truncate text-sm font-semibold">{open?.title ?? "New thread"}</h1>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-24">
          <ThreadView />
        </main>
      </div>
    </div>
  )
}

function Card({
  thread,
  owner,
  current,
  onOpen,
}: {
  thread: ProtoThread
  owner: string | undefined
  current: boolean
  onOpen: () => void
}) {
  const status = STATUS[thread.status]
  return (
    <button
      type="button"
      aria-current={current ? "page" : undefined}
      onClick={onOpen}
      className="flex flex-col gap-1 rounded-lg border border-transparent px-3 py-2 text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:border-border aria-[current=page]:bg-card"
    >
      <span className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{thread.title}</span>
        <span className="font-mono text-xs text-muted-foreground">{thread.when}</span>
      </span>
      <span className="line-clamp-2 text-xs text-muted-foreground">{thread.preview}</span>
      {(status !== undefined || thread.routine !== undefined || owner !== undefined) && (
        <span className="flex flex-wrap items-center gap-1.5 pt-0.5">
          {status !== undefined && (
            <span className={`flex items-center gap-1 font-mono text-xs ${status.tone}`}>
              <status.icon
                className={`size-3.5 ${thread.status === "running" ? "animate-spin" : ""}`}
              />
              {status.label}
            </span>
          )}
          {thread.routine !== undefined && <Badge variant="outline">{thread.routine}</Badge>}
          {owner !== undefined && <Badge variant="secondary">{owner}</Badge>}
        </span>
      )}
    </button>
  )
}
