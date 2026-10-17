// PROTOTYPE layout C, "Top bar and inbox": no sidebar. A top bar switches the teammate and its
// pages; the thread sits centred at reading width; a right column gathers everything that waits
// on you, across every instance. Below lg the inbox becomes a button in the bar.
import { ChevronDownIcon, InboxIcon, ShieldAlertIcon, CircleAlertIcon } from "lucide-react"

import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

import { instances, threads } from "./fixtures"
import { HUB_PAGES, ThreadRow, ThreadView } from "./layout-parts"
import { initial } from "./sheet"

export const name = "Top bar and inbox"

const TABS = ["Threads", "Memory", "Stats", "Config"]

export function LayoutC() {
  const atlas = instances[0]
  return (
    <div className="flex h-svh flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 md:gap-4 md:px-4">
        <a
          href="#home"
          aria-label="kinby home"
          className="grid size-9 place-items-center rounded-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <KinbyMark className="size-6" />
        </a>
        <Button variant="ghost" aria-label="Switch instance, Atlas selected">
          <Avatar shape={atlas.avatar.shape} size="sm" aria-hidden>
            <AvatarFallback variant={atlas.avatar.color}>{initial(atlas.name)}</AvatarFallback>
          </Avatar>
          {atlas.name}
          <ChevronDownIcon data-icon="inline-end" />
        </Button>
        <nav aria-label="Atlas" className="hidden items-center gap-1 md:flex">
          {TABS.map((tab, index) => (
            <a
              key={tab}
              href="#tab"
              aria-current={index === 0 ? "page" : undefined}
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent aria-[current=page]:text-foreground"
            >
              {tab}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" className="lg:hidden">
            <InboxIcon data-icon="inline-start" />
            Needs you
            <Badge variant="attention">2</Badge>
          </Button>
          <nav aria-label="Hub" className="hidden items-center gap-1 md:flex">
            {HUB_PAGES.map((page) => (
              <Button key={page.label} variant="ghost" size="icon" aria-label={page.label}>
                <page.icon />
              </Button>
            ))}
          </nav>
        </div>
      </header>
      {/* Below md the pages move to a strip of their own that scrolls sideways. */}
      <nav aria-label="Atlas" className="flex gap-1 overflow-x-auto border-b px-3 py-1.5 md:hidden">
        {[...TABS, ...HUB_PAGES.map((page) => page.label)].map((tab, index) => (
          <a
            key={tab}
            href="#tab"
            aria-current={index === 0 ? "page" : undefined}
            className="shrink-0 rounded-md px-3 py-1.5 text-sm text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent aria-[current=page]:text-foreground"
          >
            {tab}
          </a>
        ))}
      </nav>
      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto pb-24">
          <div className="mx-auto flex max-w-3xl items-baseline gap-3 px-4 pt-6 md:px-6">
            <h1 className="text-2xl font-semibold">Dentist and Ana</h1>
            <span className="font-mono text-xs text-muted-foreground">2 turns · today</span>
          </div>
          <ThreadView />
        </main>
        <aside
          aria-labelledby="c-inbox"
          className="hidden w-80 shrink-0 flex-col gap-6 overflow-y-auto border-l bg-sidebar p-4 lg:flex"
        >
          <section className="flex flex-col gap-2">
            <h2 id="c-inbox" className="flex items-center gap-2 text-base font-semibold">
              Needs you <Badge variant="attention">2</Badge>
            </h2>
            <ul className="flex flex-col gap-2">
              <li>
                <a
                  href="#approval"
                  className="flex flex-col gap-1 rounded-lg bg-card p-3 ring-1 ring-foreground/10 outline-none hover:ring-kin focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                    <ShieldAlertIcon className="size-3.5" /> atlas · approval
                  </span>
                  <span className="text-sm font-medium">Send an email to Ana?</span>
                </a>
              </li>
              <li>
                <a
                  href="#failed"
                  className="flex flex-col gap-1 rounded-lg bg-card p-3 ring-1 ring-foreground/10 outline-none hover:ring-kin focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="flex items-center gap-1.5 font-mono text-xs text-destructive">
                    <CircleAlertIcon className="size-3.5" /> atlas · failed
                  </span>
                  <span className="text-sm font-medium">Invoice for Marta</span>
                </a>
              </li>
            </ul>
          </section>
          <section className="flex flex-col gap-2">
            <h2 className="text-base font-semibold">Recent threads</h2>
            <ul className="flex flex-col gap-1">
              {threads.slice(1).map((thread) => (
                <li key={thread.id}>
                  <ThreadRow thread={thread} />
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  )
}
