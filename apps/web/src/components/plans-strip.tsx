import type { PlanLimit, PlanUse, UsageSource } from "@kinby/contract"
import { cn } from "cn"
import { ClockIcon, TriangleAlertIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { SOURCE_LABELS } from "@/lib/stats"

/**
 * One badge per subscription with its runs in each plan window, whatever range the page shows. A
 * limited plan's badge turns red and says when it resets. Counts only, never what is left.
 */
export function PlansStrip({ planUse, limits }: { planUse: PlanUse[]; limits: PlanLimit[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {plans(planUse, limits).map((plan) => (
        <PlanBadge key={plan.source} {...plan} />
      ))}
    </div>
  )
}

/**
 * One row per subscription: its name, then its runs in each plan window, and when a limited plan
 * resets. Counts only, never what is left.
 */
export function PlanRows({ planUse, limits }: { planUse: PlanUse[]; limits: PlanLimit[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {plans(planUse, limits).map(({ source, windows, limit }) => (
        <li key={source} className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
          <span className="font-medium">{SOURCE_LABELS[source]}</span>
          <span
            className={cn(
              "tabular-nums",
              limit === undefined ? "text-muted-foreground" : "text-destructive",
            )}
          >
            {runsLine(windows)}
            {limit !== undefined && ` · limited until ${localTime(limit.resets_at)}`}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Each subscription with its plan windows, and its limit when it is limited. */
function plans(planUse: PlanUse[], limits: PlanLimit[]) {
  return [...new Set(planUse.map((use) => use.usage_source))].map((source) => ({
    source,
    windows: planUse.filter((use) => use.usage_source === source),
    limit: limits.find((limit) => limit.usage_source === source),
  }))
}

function PlanBadge({
  source,
  windows,
  limit,
}: {
  source: UsageSource
  windows: PlanUse[]
  limit: PlanLimit | undefined
}) {
  return (
    <Badge
      variant={limit === undefined ? "outline" : "destructive"}
      // A long line of windows wraps on a phone instead of losing its end, the reset time.
      className="h-auto min-h-5 text-left whitespace-normal"
    >
      {limit === undefined ? (
        <ClockIcon data-icon="inline-start" />
      ) : (
        <TriangleAlertIcon data-icon="inline-start" />
      )}
      {`${SOURCE_LABELS[source]} runs: ${runsLine(windows)}`}
      {limit !== undefined && ` · limited until ${localTime(limit.resets_at)}`}
    </Badge>
  )
}

/** A plan's runs in each of its windows, as `4 in 5h · 21 in 7d`. */
function runsLine(windows: PlanUse[]): string {
  return windows.map((use) => `${use.runs} in ${windowLabel(use.duration_seconds)}`).join(" · ")
}

/** A plan window's length, in days when it is whole days and in hours otherwise. */
function windowLabel(seconds: number): string {
  return seconds % 86_400 === 0 ? `${seconds / 86_400}d` : `${seconds / 3_600}h`
}

/** The reader's own clock time of an instant, as HH:MM. */
function localTime(instant: string): string {
  return new Date(instant).toLocaleTimeString("en", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  })
}
