// PROTOTYPE: what every layout variant shows inside its frame. The approved components (variant E)
// plus thread rows and the hub's own pages. The frame around them is each variant's own.
import {
  ChartColumnIcon,
  CheckIcon,
  CircleAlertIcon,
  KeyRoundIcon,
  LibraryIcon,
  ShieldAlertIcon,
  type LucideIcon,
} from "lucide-react"

import { Approval, Turn } from "./components-e-merged"
import { threads, turns } from "./fixtures"

export const HUB_PAGES: { label: string; icon: LucideIcon }[] = [
  { label: "Usage", icon: ChartColumnIcon },
  { label: "Library", icon: LibraryIcon },
  { label: "Secrets", icon: KeyRoundIcon },
]

export const THREAD_STATUS: Record<
  (typeof threads)[number]["status"],
  { label: string; icon: LucideIcon; tone: string }
> = {
  awaiting_approval: { label: "Needs you", icon: ShieldAlertIcon, tone: "text-foreground" },
  done: { label: "Done", icon: CheckIcon, tone: "text-muted-foreground" },
  failed: { label: "Failed", icon: CircleAlertIcon, tone: "text-destructive" },
}

export const ROUTINES = [
  { name: "Morning brief", when: "Weekdays 8:00", cron: "0 8 * * 1-5", last: "Ran today, 8:00" },
  { name: "Inbox sweep", when: "Every 2 hours", cron: "0 */2 * * *", last: "Ran 10:00" },
  { name: "Weekly review", when: "Fridays 17:00", cron: "0 17 * * 5", last: "Ran Fri" },
]

/** The open thread: its turns, then the approval it is parked on, in the composer's place. */
export function ThreadView() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-10 px-4 py-6 md:px-6">
      {turns.map((turn) => (
        <Turn key={turn.id} turn={turn} />
      ))}
      <Approval />
    </div>
  )
}

/** One thread in a list: its title, when, and its status as an icon and a word. */
export function ThreadRow({
  thread,
  current,
}: {
  thread: (typeof threads)[number]
  current?: boolean
}) {
  const status = THREAD_STATUS[thread.status]
  return (
    <a
      href="#thread"
      aria-current={current ? "page" : undefined}
      className="flex flex-col gap-1 rounded-lg px-3 py-2 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
    >
      <span className="flex items-center gap-2">
        {"routine" in thread && <span aria-hidden className="size-2 rounded-full bg-ember" />}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {"routine" in thread && <span className="sr-only">Routine: </span>}
          {thread.title}
        </span>
        <span className="font-mono text-xs text-muted-foreground">{thread.when}</span>
      </span>
      <span className={`flex items-center gap-1.5 font-mono text-xs ${status.tone}`}>
        <status.icon className="size-3.5" />
        {status.label}
      </span>
    </a>
  )
}
