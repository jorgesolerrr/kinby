// PROTOTYPE layout B, "Three panes": mail-client structure. A slim rail of instance avatars
// (switching teammates is one click), a column of the selected instance's threads with their
// status, then the thread. Below md the thread list folds behind a button.
import { ListIcon, PlusIcon, SearchIcon } from "lucide-react"
import { useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

import { instances, threads } from "./fixtures"
import { HUB_PAGES, ThreadRow, ThreadView } from "./layout-parts"
import { initial, STATE } from "./sheet"

export const name = "Three panes"

export function LayoutB() {
  const [listOpen, setListOpen] = useState(false)
  const atlas = instances[0]
  return (
    <div className="flex h-svh">
      <nav
        aria-label="Instances"
        className="flex w-16 shrink-0 flex-col items-center gap-2 border-r bg-sidebar py-3"
      >
        <a
          href="#home"
          aria-label="kinby home"
          className="mb-2 grid size-10 place-items-center rounded-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <KinbyMark className="size-7" />
        </a>
        {instances.map((instance, index) => (
          <Tooltip key={instance.id}>
            <TooltipTrigger
              render={
                <a
                  href="#instance"
                  aria-current={index === 0 ? "page" : undefined}
                  aria-label={`${instance.name}, ${
                    instance.needsYou > 0
                      ? `${instance.needsYou} waiting for you`
                      : STATE[instance.state].label
                  }`}
                  className="relative grid size-10 place-items-center rounded-lg outline-none hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-sidebar-accent"
                >
                  <Avatar shape={instance.avatar.shape} aria-hidden>
                    <AvatarFallback variant={instance.avatar.color}>
                      {initial(instance.name)}
                    </AvatarFallback>
                  </Avatar>
                  {instance.needsYou > 0 && (
                    <span className="absolute top-0.5 right-0.5 size-2.5 rounded-full bg-ember ring-2 ring-sidebar" />
                  )}
                  {index === 0 && (
                    <span className="absolute top-2 bottom-2 -left-3 w-1 rounded-full bg-kin" />
                  )}
                </a>
              }
            />
            <TooltipContent side="right">{instance.name}</TooltipContent>
          </Tooltip>
        ))}
        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="ghost" size="icon-lg" aria-label="New instance">
                <PlusIcon />
              </Button>
            }
          />
          <TooltipContent side="right">New instance</TooltipContent>
        </Tooltip>
        <div className="mt-auto flex flex-col gap-1">
          {HUB_PAGES.map((page) => (
            <Tooltip key={page.label}>
              <TooltipTrigger
                render={
                  <Button variant="ghost" size="icon-lg" aria-label={page.label}>
                    <page.icon />
                  </Button>
                }
              />
              <TooltipContent side="right">{page.label}</TooltipContent>
            </Tooltip>
          ))}
        </div>
      </nav>
      <aside
        aria-label="Atlas's threads"
        className={`${listOpen ? "flex" : "hidden"} w-72 shrink-0 flex-col border-r md:flex`}
      >
        <div className="flex flex-col gap-3 border-b p-3">
          <div className="flex items-center gap-2">
            <h2 className="flex-1 text-lg font-semibold">{atlas.name}</h2>
            <Button size="sm" variant="outline">
              <PlusIcon data-icon="inline-start" />
              New thread
            </Button>
          </div>
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input aria-label="Search threads" placeholder="Search threads" className="pl-8" />
          </div>
        </div>
        <ul className="flex flex-col gap-1 overflow-y-auto p-2">
          {threads.map((thread, index) => (
            <li key={thread.id}>
              <ThreadRow thread={thread} current={index === 0} />
            </li>
          ))}
        </ul>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={listOpen ? "Hide threads" : "Show threads"}
            aria-expanded={listOpen}
            onClick={() => setListOpen(!listOpen)}
          >
            <ListIcon />
          </Button>
          <h1 className="truncate text-sm font-semibold">Dentist and Ana</h1>
          <span className="font-mono text-xs text-muted-foreground">2 turns</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-24">
          <ThreadView />
        </main>
      </div>
    </div>
  )
}
