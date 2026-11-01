import { RefreshCwIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { bucketSize, RANGES, type Range } from "@/lib/stats"

interface RangeProps {
  range: Range
  onRange: (range: Range) => void
  onRefresh: () => void
}

/** A stats view's title, its 7, 30, and 90-day range picker, and Refresh. */
export function RangeHeader({ title, ...controls }: RangeProps & { title: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">
        <RangeControls {...controls} />
      </div>
    </div>
  )
}

/** The 7, 30, and 90-day range picker, its buckets, and Refresh. */
export function RangeControls({ range, onRange, onRefresh }: RangeProps) {
  return (
    <>
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
      <span className="text-xs text-muted-foreground">
        {bucketSize(range) === "week" ? "Weekly" : "Daily"} buckets, UTC
      </span>
      <Button size="sm" variant="ghost" onClick={onRefresh}>
        <RefreshCwIcon data-icon="inline-start" />
        Refresh
      </Button>
    </>
  )
}
