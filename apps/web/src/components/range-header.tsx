import { RefreshCwIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { bucketSize, RANGES, type Range } from "@/lib/stats"

interface RangeProps {
  range: Range
  onRange: (range: Range) => void
  onRefresh: () => void
}

/** A stats view's 7, 30, and 90-day range picker, its buckets, and Refresh. */
export function RangeHeader({ range, onRange, onRefresh }: RangeProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <RangeToggle range={range} onRange={onRange} />
      <span className="text-xs text-muted-foreground">
        {bucketSize(range) === "week" ? "Weekly" : "Daily"} buckets, UTC
      </span>
      <Button size="sm" variant="ghost" onClick={onRefresh}>
        <RefreshCwIcon data-icon="inline-start" />
        Refresh
      </Button>
    </div>
  )
}

/** The 7, 30, and 90-day range picker. */
export function RangeToggle({ range, onRange }: Omit<RangeProps, "onRefresh">) {
  return (
    <ToggleGroup
      aria-label="Range"
      size="sm"
      variant="outline"
      value={[String(range)]}
      onValueChange={([picked]) => {
        const next = RANGES.find((days) => String(days) === picked)
        if (next !== undefined) onRange(next)
      }}
    >
      {RANGES.map((days) => (
        <ToggleGroupItem key={days} value={String(days)}>
          {days} days
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
