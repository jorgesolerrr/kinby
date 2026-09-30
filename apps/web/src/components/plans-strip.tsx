import type { PlanLimit, PlanUse, UsageSource } from "@kinby/contract"
import { ClockIcon, TriangleAlertIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { SOURCE_LABELS } from "@/lib/stats"

/**
 * One badge per subscription with its runs in each plan window, whatever range the page shows. A
 * limited plan's badge turns red and says when it resets. Counts only, never what is left.
 */
export function PlansStrip({ planUse, limits }: { planUse: PlanUse[]; limits: PlanLimit[] }) {
  const sources = [...new Set(planUse.map((use) => use.usage_source))]
  return (
    <div className="flex flex-wrap items-center gap-2">
      {sources.map((source) => (
        <PlanBadge
          key={source}
          source={source}
          windows={planUse.filter((use) => use.usage_source === source)}
          limit={limits.find((limit) => limit.usage_source === source)}
        />
      ))}
    </div>
  )
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
  const runs = windows
    .map((use) => `${use.runs} in ${windowLabel(use.duration_seconds)}`)
    .join(" · ")
  return (
    <Badge variant={limit === undefined ? "outline" : "destructive"}>
      {limit === undefined ? (
        <ClockIcon data-icon="inline-start" />
      ) : (
        <TriangleAlertIcon data-icon="inline-start" />
      )}
      {`${SOURCE_LABELS[source]} runs: ${runs}`}
      {limit !== undefined && ` · limited until ${localTime(limit.resets_at)}`}
    </Badge>
  )
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
