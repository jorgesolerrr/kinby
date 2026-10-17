// PROTOTYPE: pieces every round-two variant shows inside its own frame. The frame is each variant's.
import {
  ChartColumnIcon,
  CircleAlertIcon,
  KeyRoundIcon,
  LibraryIcon,
  LoaderCircleIcon,
  PlusIcon,
  ShieldAlertIcon,
  type LucideIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"

import { Approval, Turn } from "./components-e-merged"
import { instances, turns, type ProtoInstance, type ThreadState } from "./fixtures"
import { initial, STATE } from "./sheet"

export const HUB_PAGES: { label: string; icon: LucideIcon }[] = [
  { label: "Usage", icon: ChartColumnIcon },
  { label: "Library", icon: LibraryIcon },
  { label: "Secrets", icon: KeyRoundIcon },
]

/** What a thread's status shows. An idle thread needs no look, so it shows nothing. */
export const STATUS: Record<
  ThreadState,
  { label: string; icon: LucideIcon; tone: string } | undefined
> = {
  idle: undefined,
  running: { label: "Working", icon: LoaderCircleIcon, tone: "text-muted-foreground" },
  awaiting_approval: { label: "Needs you", icon: ShieldAlertIcon, tone: "text-foreground" },
  failed: { label: "Failed", icon: CircleAlertIcon, tone: "text-destructive" },
}

export function AgentAvatar({ agent, size }: { agent: ProtoInstance; size?: "sm" | "lg" }) {
  return (
    <Avatar shape={agent.avatar.shape} size={size} aria-hidden>
      <AvatarFallback variant={agent.avatar.color}>{initial(agent.name)}</AvatarFallback>
    </Avatar>
  )
}

/** The agent's second line: what waits on the user, or else its state. */
export function agentLine(agent: ProtoInstance) {
  return agent.needsYou > 0 ? `${agent.needsYou} waiting for you` : STATE[agent.state].label
}

/** The open thread: its turns, then the approval it is parked on, in the composer's place. */
export function ThreadView({ width = "max-w-3xl" }: { width?: string }) {
  return (
    <div className={`mx-auto flex w-full ${width} flex-col gap-10 px-4 py-6 md:px-8`}>
      {turns.map((turn) => (
        <Turn key={turn.id} turn={turn} />
      ))}
      <Approval />
    </div>
  )
}

/**
 * The agents sidebar the hub pages sit beside: the mark, the agents with their second line, then
 * the hub's own pages and the user at the foot. Library and Secrets judge their space against it.
 */
export function HubFrame({ active, children }: { active: string; children: ReactNode }) {
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
          {instances.map((agent) => (
            <a
              key={agent.id}
              href="#agent"
              className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent"
            >
              <AgentAvatar agent={agent} size="sm" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{agent.name}</span>
              {agent.needsYou > 0 && <span className="size-2 rounded-full bg-ember" />}
            </a>
          ))}
          <a
            href="#new"
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent"
          >
            <PlusIcon className="size-4" />
            New instance
          </a>
        </div>
        <div className="mt-auto flex flex-col gap-0.5">
          {HUB_PAGES.map((page) => (
            <a
              key={page.label}
              href="#page"
              aria-current={page.label === active ? "page" : undefined}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium"
            >
              <page.icon className="size-4" />
              {page.label}
            </a>
          ))}
        </div>
      </nav>
      <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
    </div>
  )
}
