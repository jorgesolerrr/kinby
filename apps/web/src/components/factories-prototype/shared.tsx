// PROTOTYPE (#1299), throwaway. What the three variants share: the in-URL navigation, the variant
// switcher, the sidebar's frame with teammates only, and small pieces such as a status badge.
import { cn } from "cn"
import {
  BotIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CodeIcon,
  HandIcon,
  LibraryIcon,
  SearchIcon,
  SquarePenIcon,
  TerminalIcon,
  TimerIcon,
  UserIcon,
} from "lucide-react"
import type * as React from "react"
import { useEffect } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"

import {
  STATUS_LABEL,
  type Entry,
  type Outcome,
  type Run,
  type StepKind,
  type Teammate,
} from "./data"
import { go, param, setParam, useAt, useParam } from "./nav"

/** A link that opens a screen of the prototype. */
export function To({
  at,
  className,
  children,
}: {
  at: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <a
      href={`?variant=${param("variant") ?? "A"}&at=${at}`}
      className={cn("underline-offset-4 hover:underline", className)}
      onClick={(event) => {
        event.preventDefault()
        go(at)
      }}
    >
      {children}
    </a>
  )
}

/** The floating bar that cycles variants. Not part of any design. */
export function PrototypeSwitcher({ variants }: { variants: { key: string; name: string }[] }) {
  const current = useParam("variant") ?? variants[0].key
  const index = Math.max(
    0,
    variants.findIndex((v) => v.key === current),
  )
  const cycle = (by: number) =>
    setParam("variant", variants[(index + by + variants.length) % variants.length].key, false)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.closest("input, textarea, [contenteditable]")) return
      if (event.key === "ArrowLeft") cycle(-1)
      if (event.key === "ArrowRight") cycle(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })
  if (import.meta.env.PROD) return null
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full bg-foreground p-1 text-background shadow-xl">
      <button
        type="button"
        aria-label="Previous variant"
        className="rounded-full p-1.5 hover:bg-background/20"
        onClick={() => cycle(-1)}
      >
        <ChevronLeftIcon className="size-4" />
      </button>
      <span className="px-2 font-mono text-xs">
        {variants[index].key} ({variants[index].name})
      </span>
      <button
        type="button"
        aria-label="Next variant"
        className="rounded-full p-1.5 hover:bg-background/20"
        onClick={() => cycle(1)}
      >
        <ChevronRightIcon className="size-4" />
      </button>
    </div>
  )
}

export function TeammateAvatar({ who, size }: { who: Teammate; size?: "sm" | "default" }) {
  return (
    <Avatar shape={who.shape} size={size} aria-hidden>
      <AvatarFallback variant={who.color}>{who.name.charAt(0)}</AvatarFallback>
    </Avatar>
  )
}

/**
 * The app's shell with teammates only: the header menu with `extraNav` under Library, the
 * teammates, then `belowTeammates`. The page goes in `children`.
 */
export function Shell({
  teammates,
  extraNav,
  renderTeammate,
  belowTeammates,
  children,
}: {
  teammates: Teammate[]
  extraNav?: React.ReactNode
  renderTeammate?: (who: Teammate) => React.ReactNode
  belowTeammates?: React.ReactNode
  children: React.ReactNode
}) {
  const [kind, id] = useAt()
  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar collapsible="offcanvas">
          <SidebarHeader>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton size="lg" onClick={() => go("area")}>
                  <span className="flex size-8 shrink-0 items-center justify-center">
                    <KinbyMark className="size-7!" />
                  </span>
                  <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton>
                  <SquarePenIcon />
                  <span>New thread</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton>
                  <SearchIcon />
                  <span>Search</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton>
                  <LibraryIcon />
                  <span>Library</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {extraNav}
            </SidebarMenu>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Teammates</SidebarGroupLabel>
              <SidebarMenu>
                {teammates.map((who) =>
                  renderTeammate ? (
                    renderTeammate(who)
                  ) : (
                    <SidebarMenuItem key={who.id}>
                      <SidebarMenuButton
                        isActive={kind === "owner" && id === who.id}
                        onClick={() => go(`owner:${who.id}`)}
                      >
                        <TeammateAvatar who={who} size="sm" />
                        <span>{who.name}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ),
                )}
              </SidebarMenu>
            </SidebarGroup>
            {belowTeammates}
          </SidebarContent>
          <SidebarFooter>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton>
                  <UserIcon />
                  <span>Jorge</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarFooter>
        </Sidebar>
        <SidebarInset className="min-w-0">
          <header className="flex h-12 shrink-0 items-center gap-2 px-3">
            <SidebarTrigger />
            <Badge variant="outline">PROTOTYPE #1299, fixed data</Badge>
          </header>
          <div className="flex min-h-0 flex-1 flex-col pb-20">{children}</div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}

/** A run's status, or what it waits on from the user when it does. */
export function RunBadge({ run }: { run: Run }) {
  if (run.waiting?.kind === "approval") return <Badge variant="attention">Needs approval</Badge>
  if (run.waiting?.kind === "takeover") return <Badge variant="attention">Needs a takeover</Badge>
  if (run.status === "needs-human") return <Badge variant="attention">Needs a human</Badge>
  const variant =
    run.status === "running" ? "success" : run.status === "done" ? "secondary" : "outline"
  return <Badge variant={variant}>{STATUS_LABEL[run.status]}</Badge>
}

export function StepIcon({ kind, className }: { kind: StepKind; className?: string }) {
  const Icon =
    kind === "agent"
      ? BotIcon
      : kind === "command"
        ? TerminalIcon
        : kind === "code"
          ? CodeIcon
          : kind === "wait"
            ? TimerIcon
            : HandIcon
  return <Icon className={cn("size-4 shrink-0", className)} />
}

const OUTCOME_VARIANT: Record<Outcome, "success" | "destructive" | "attention" | "outline"> = {
  running: "success",
  clean: "outline",
  approved: "outline",
  failed: "destructive",
  "timed out": "destructive",
  "needs human": "attention",
  asked: "attention",
  waiting: "attention",
  "sent back": "outline",
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <Badge variant={OUTCOME_VARIANT[outcome]}>{outcome}</Badge>
}

/** One attempt's transcript, whichever runner made it. */
export function Transcript({ entries }: { entries: Entry[] }) {
  return (
    <ol className="flex flex-col gap-1.5 text-sm">
      {entries.map((entry, index) => (
        <li key={index}>
          <TranscriptEntry entry={entry} />
        </li>
      ))}
    </ol>
  )
}

function TranscriptEntry({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "say":
      return <p>{entry.text}</p>
    case "user":
      return <p className="rounded-md bg-muted px-2 py-1 text-muted-foreground">{entry.text}</p>
    case "tool":
      return (
        <p className="font-mono text-xs text-muted-foreground">
          <span className="text-foreground">{entry.name}</span> {entry.text}
        </p>
      )
    case "cmd":
      return (
        <div className="font-mono text-xs">
          <p>
            <span className={entry.exit === 0 ? "text-success" : "text-destructive"}>
              {entry.exit === 0 ? "✓" : `✗ exit ${entry.exit}`}
            </span>{" "}
            $ {entry.text}
          </p>
          {entry.tail !== undefined && (
            <pre className="mt-1 rounded-md bg-muted p-2 whitespace-pre-wrap text-muted-foreground">
              {entry.tail}
            </pre>
          )}
        </div>
      )
    case "call":
      return (
        <p className="font-mono text-xs">
          {entry.text}
          {entry.values !== undefined && (
            <span className="text-muted-foreground">
              {" → "}
              {Object.entries(entry.values)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}
            </span>
          )}
        </p>
      )
    case "event":
      return <p className="text-xs text-muted-foreground italic">{entry.text}</p>
  }
}

/** A teammate's page as the app draws it today, its Overview tab holding `children`. */
export function OwnerFrame({ who, children }: { who: Teammate; children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 md:px-8">
      <div className="flex items-center gap-3 py-6">
        <TeammateAvatar who={who} />
        <div>
          <h1 className="font-heading text-xl font-semibold">{who.name}</h1>
          <p className="text-sm text-muted-foreground">
            {who.home ? "Home instance · " : ""}Running · claude-opus-5-5
          </p>
        </div>
      </div>
      <div className="mb-6 flex gap-4 border-b text-sm">
        {["Overview", "Config", "Routines", "Memory", "Stats"].map((tab, index) => (
          <span
            key={tab}
            className={cn(
              "pb-2",
              index === 0 ? "border-b-2 border-foreground font-medium" : "text-muted-foreground",
            )}
          >
            {tab}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-6">
        {children}
        <section>
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">Recent threads</h2>
          <ul className="flex flex-col gap-1 text-sm">
            {who.threads.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}

/** A page's centred column with its title. */
export function Page({
  title,
  crumbs,
  actions,
  children,
}: {
  title: React.ReactNode
  crumbs?: React.ReactNode
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-6 md:px-8">
      {crumbs !== undefined && <div className="mb-1 text-sm text-muted-foreground">{crumbs}</div>}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-heading text-2xl font-semibold">{title}</h1>
        {actions !== undefined && <div className="flex gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
