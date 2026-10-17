// PROTOTYPE layout D, "Instance home, thread beside it": the instance's page is the hub of the
// app. Its figures, what waits on you, its threads and its routines sit on the left; a thread
// opens in a pane beside the page instead of replacing it. Below xl the two stack.
import { PlusIcon, RepeatIcon, XIcon } from "lucide-react"

import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

import { Figures, InstanceRows } from "./components-e-merged"
import { instances, threads } from "./fixtures"
import { HUB_PAGES, ROUTINES, ThreadRow, ThreadView } from "./layout-parts"
import { initial } from "./sheet"

export const name = "Instance home, thread beside it"

export function LayoutD() {
  const atlas = instances[0]
  return (
    <div className="flex min-h-svh flex-col md:h-svh md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b bg-sidebar p-3 md:w-64 md:border-r md:border-b-0">
        <a
          href="#home"
          className="flex items-center gap-2 rounded-lg px-2 py-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <KinbyMark className="size-7" />
          <span className="font-heading text-lg font-semibold">kinby</span>
        </a>
        <InstanceRows selected="atlas" />
        <Button variant="ghost" className="justify-start">
          <PlusIcon data-icon="inline-start" />
          New instance
        </Button>
        <nav aria-label="Hub" className="mt-auto flex flex-col gap-1">
          {HUB_PAGES.map((page) => (
            <Button key={page.label} variant="ghost" className="justify-start">
              <page.icon data-icon="inline-start" />
              {page.label}
            </Button>
          ))}
        </nav>
      </aside>
      <div className="grid min-h-0 flex-1 xl:grid-cols-5">
        <main className="flex flex-col gap-8 overflow-y-auto p-4 pb-24 md:p-6 xl:col-span-2 xl:border-r">
          <header className="flex items-center gap-3">
            <Avatar shape={atlas.avatar.shape} size="lg" aria-hidden>
              <AvatarFallback variant={atlas.avatar.color}>{initial(atlas.name)}</AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-1 flex-col">
              <h1 className="text-2xl font-semibold">{atlas.name}</h1>
              <p className="font-mono text-xs text-muted-foreground">
                {atlas.role} · running · core 0.14
              </p>
            </div>
            <Button variant="outline" size="sm">
              Config
            </Button>
          </header>
          <Figures />
          <section aria-labelledby="d-threads" className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h2 id="d-threads" className="text-base font-semibold">
                Threads
              </h2>
              <Button size="sm" variant="outline">
                <PlusIcon data-icon="inline-start" />
                New thread
              </Button>
            </div>
            <ul className="flex flex-col gap-1">
              {threads.map((thread, index) => (
                <li key={thread.id}>
                  <ThreadRow thread={thread} current={index === 0} />
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="d-routines" className="flex flex-col gap-2">
            <h2 id="d-routines" className="text-base font-semibold">
              Routines
            </h2>
            <ul className="flex flex-col divide-y rounded-lg border">
              {ROUTINES.map((routine) => (
                <li key={routine.name} className="flex items-center gap-3 p-3">
                  <RepeatIcon className="size-4 text-muted-foreground" />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-sm font-medium">{routine.name}</span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {routine.cron} · {routine.when}
                    </span>
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{routine.last}</span>
                </li>
              ))}
            </ul>
          </section>
        </main>
        <section
          aria-labelledby="d-thread"
          className="flex min-h-0 flex-col border-t xl:col-span-3 xl:border-t-0"
        >
          <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
            <h2 id="d-thread" className="truncate text-sm font-semibold">
              Dentist and Ana
            </h2>
            <Badge variant="attention">Needs you</Badge>
            <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="Close thread">
              <XIcon />
            </Button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto pb-24">
            <ThreadView />
          </div>
        </section>
      </div>
    </div>
  )
}
