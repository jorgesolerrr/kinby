import { cn } from "cn"
import type * as React from "react"

/**
 * Figures side by side in one bordered strip, a hairline between each. The gap shows the border
 * color through, so the lines hold at any column count; pass the columns as `className`.
 */
export function TileStrip({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("grid gap-px overflow-hidden rounded-lg border bg-border", className)}
      {...props}
    />
  )
}

/** One figure over a range, under its name and over a note that breaks it down. */
export function Tile({ title, value, note }: { title: string; value: string; note: string }) {
  return (
    <section aria-label={title} className="flex flex-col gap-1 bg-card p-4">
      <p className="font-mono text-xs text-muted-foreground uppercase">{title}</p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      <p className="font-mono text-xs text-muted-foreground">{note}</p>
    </section>
  )
}
