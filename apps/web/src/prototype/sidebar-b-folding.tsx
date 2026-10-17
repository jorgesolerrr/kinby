// PROTOTYPE sidebar B, "Folding columns": the same two columns, but each folds. The agents sidebar
// folds to a rail of avatars and the threads column folds away, so a long thread can take the
// whole window. The agent's own pages are tabs over the content, not icons in the column, and
// threads that need the user are pinned above the rest instead of filtered.
import {
  MessageSquarePlusIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react"
import { useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

import { instances, threadsByAgent, type ProtoThread } from "./fixtures"
import { AgentAvatar, agentLine, HUB_PAGES, STATUS, ThreadView } from "./parts"

export const name = "Folding columns"

const TABS = ["Threads", "Memory", "Stats", "Settings"] as const

export function SidebarB() {
  const [agentId, setAgentId] = useState("atlas")
  const [threadId, setThreadId] = useState("a1")
  const [query, setQuery] = useState("")
  const [railed, setRailed] = useState(false)
  const [listShown, setListShown] = useState(true)
  const [tab, setTab] = useState<(typeof TABS)[number]>("Threads")
  const agent = instances.find((each) => each.id === agentId) ?? instances[0]
  const q = query.trim().toLowerCase()
  const threads = threadsByAgent[agent.id].filter(
    (thread) => q === "" || `${thread.title} ${thread.preview}`.toLowerCase().includes(q),
  )
  const pinned = threads.filter(
    (thread) => thread.status === "awaiting_approval" || thread.status === "failed",
  )
  const rest = threads.filter((thread) => !pinned.includes(thread))
  const open = threadsByAgent[agent.id].find((thread) => thread.id === threadId)

  return (
    <div className="flex h-svh">
      <nav
        aria-label="kinby"
        className={`flex shrink-0 flex-col gap-4 border-r bg-sidebar p-2 text-sidebar-foreground transition-[width] ${railed ? "w-14 items-center" : "w-60"}`}
      >
        <div className="flex items-center gap-2 px-1 pt-1">
          <KinbyMark className="size-7 shrink-0" />
          {!railed && (
            <span className="flex-1 font-heading text-lg font-semibold tracking-tight">kinby</span>
          )}
        </div>
        <div className="flex flex-col gap-0.5">
          {!railed && (
            <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">Instances</p>
          )}
          {instances.map((each) => {
            const row = (
              <button
                type="button"
                aria-label={railed ? `${each.name}, ${agentLine(each)}` : undefined}
                aria-current={each.id === agent.id ? "page" : undefined}
                onClick={() => {
                  setAgentId(each.id)
                  setThreadId(threadsByAgent[each.id][0]?.id ?? "")
                  setTab("Threads")
                }}
                className="relative flex items-center gap-2.5 rounded-md p-1.5 text-left outline-none hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-sidebar-accent"
              >
                <AgentAvatar agent={each} />
                {!railed && (
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium">{each.name}</span>
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {agentLine(each)}
                    </span>
                  </span>
                )}
                {each.needsYou > 0 && (
                  <span
                    className={
                      railed
                        ? "absolute top-1 right-1 size-2.5 rounded-full bg-ember ring-2 ring-sidebar"
                        : "size-2 shrink-0 rounded-full bg-ember"
                    }
                  />
                )}
              </button>
            )
            return railed ? (
              <Tooltip key={each.id}>
                <TooltipTrigger render={row} />
                <TooltipContent side="right">{each.name}</TooltipContent>
              </Tooltip>
            ) : (
              <div key={each.id} className="contents">
                {row}
              </div>
            )
          })}
          <a
            href="#new"
            aria-label="New instance"
            className="flex items-center gap-2.5 rounded-md p-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent"
          >
            <span className="grid size-8 place-items-center">
              <PlusIcon className="size-4" />
            </span>
            {!railed && "New instance"}
          </a>
        </div>
        <div className="mt-auto flex flex-col gap-0.5">
          {HUB_PAGES.map((page) => (
            <a
              key={page.label}
              href="#page"
              aria-label={page.label}
              className="flex items-center gap-2 rounded-md p-2 text-sm hover:bg-sidebar-accent"
            >
              <page.icon className="size-4" />
              {!railed && page.label}
            </a>
          ))}
          <Button
            variant="ghost"
            size="icon-sm"
            className="mt-1 self-start"
            aria-label={railed ? "Unfold the instances" : "Fold the instances"}
            onClick={() => setRailed(!railed)}
          >
            {railed ? <PanelLeftOpenIcon /> : <PanelLeftCloseIcon />}
          </Button>
        </div>
      </nav>

      {tab === "Threads" && listShown && (
        <aside
          aria-label={`${agent.name}'s threads`}
          className="flex w-72 shrink-0 flex-col border-r bg-sidebar/40"
        >
          <div className="flex gap-2 border-b p-3">
            <InputGroup>
              <InputGroupInput
                aria-label="Search threads"
                placeholder={`Search ${agent.name}'s threads`}
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
                  <Button size="icon" variant="outline" aria-label="New thread">
                    <MessageSquarePlusIcon />
                  </Button>
                }
              />
              <TooltipContent>New thread</TooltipContent>
            </Tooltip>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2">
            {pinned.length > 0 && (
              <section aria-label="Needs you" className="flex flex-col gap-0.5">
                <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">Needs you</h3>
                {pinned.map((thread) => (
                  <Row
                    key={thread.id}
                    thread={thread}
                    current={thread.id === threadId}
                    onOpen={() => setThreadId(thread.id)}
                  />
                ))}
              </section>
            )}
            <section aria-label="Recent" className="flex flex-col gap-0.5">
              <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">Recent</h3>
              {rest.map((thread) => (
                <Row
                  key={thread.id}
                  thread={thread}
                  current={thread.id === threadId}
                  onOpen={() => setThreadId(thread.id)}
                />
              ))}
              {threads.length === 0 && (
                <p className="px-2 text-sm text-muted-foreground">
                  {q === "" ? "No threads yet." : "No thread matches."}
                </p>
              )}
            </section>
          </div>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-3 border-b px-3">
          {tab === "Threads" && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={listShown ? "Hide the threads" : "Show the threads"}
              aria-expanded={listShown}
              onClick={() => setListShown(!listShown)}
            >
              {listShown ? <PanelLeftCloseIcon /> : <PanelLeftOpenIcon />}
            </Button>
          )}
          <span className="flex items-center gap-2">
            <AgentAvatar agent={agent} size="sm" />
            <span className="font-heading font-semibold">{agent.name}</span>
          </span>
          <Tabs value={tab} onValueChange={(value) => setTab(value as (typeof TABS)[number])}>
            <TabsList variant="line">
              {TABS.map((each) => (
                <TabsTrigger key={each} value={each}>
                  {each}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {tab === "Threads" && open && (
            <span className="ml-auto truncate text-sm text-muted-foreground">{open.title}</span>
          )}
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-24">
          {tab === "Threads" ? (
            <ThreadView width={listShown && !railed ? "max-w-3xl" : "max-w-4xl"} />
          ) : (
            <p className="p-8 text-sm text-muted-foreground">
              {agent.name}&apos;s {tab.toLowerCase()} page fills the whole width here, with no
              thread column beside it.
            </p>
          )}
        </main>
      </div>
    </div>
  )
}

function Row({
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
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
    >
      <span className="min-w-0 flex-1 truncate text-sm">{thread.title}</span>
      {status !== undefined && (
        <span className={`flex items-center gap-1 font-mono text-xs ${status.tone}`}>
          <status.icon
            className={`size-3.5 ${thread.status === "running" ? "animate-spin" : ""}`}
          />
          {status.label}
        </span>
      )}
      {status === undefined && (
        <span className="font-mono text-xs text-muted-foreground">{thread.when}</span>
      )}
    </button>
  )
}
