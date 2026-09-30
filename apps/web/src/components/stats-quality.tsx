import type { StatsBucketSize, StatsGetResult } from "@kinby/contract"
import { Line, LineChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { bucketLabel, type NavigationPoint, navigationTrend } from "@/lib/stats"

/** The most-called tools a quality view lists. */
const TOP_TOOLS = 10

/** How well the range's work went: what it called, what it asked, and how it was rated. */
export function Quality({ stats, by }: { stats: StatsGetResult; by: StatsBucketSize }) {
  const { total } = stats
  const trend = navigationTrend(stats.buckets, stats.records, by)
  const tools = Object.entries(total.tool_calls)
    .sort(([a, aCalls], [b, bCalls]) => bCalls - aCalls || a.localeCompare(b))
    .slice(0, TOP_TOOLS)
  const memory = total.memory_calls
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Counts
          title="Most-called tools"
          empty="No tool was called in this range."
          counts={tools}
        />
        <Counts
          title="Memory"
          counts={[
            ["Searches", memory.search ?? 0],
            ["Opens", memory.open ?? 0],
            ["Remembered", memory.remember ?? 0],
            ["Forgotten", memory.forget ?? 0],
            ["Turns without memory", total.turns_without_memory],
          ]}
        />
        <Counts
          title="Approvals"
          counts={[
            ["Asked", total.approvals_requested],
            ["Denied by policy", total.denies?.policy ?? 0],
            ["Denied by you", total.denies?.user ?? 0],
          ]}
        />
        <Counts
          title="Ratings"
          counts={[
            ["Good", total.good_ratings],
            ["Bad", total.bad_ratings],
          ]}
        />
      </div>
      {trend.some((point) => point.reads !== null) && <NavigationChart trend={trend} by={by} />}
    </>
  )
}

function Counts({
  title,
  counts,
  empty,
}: {
  title: string
  counts: [string, number][]
  empty?: string
}) {
  return (
    <section aria-label={title}>
      <Card size="sm" className="h-full">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {counts.length === 0 && <CardDescription>{empty}</CardDescription>}
        </CardHeader>
        {counts.length > 0 && (
          <CardContent>
            <dl className="flex flex-col gap-1.5">
              {counts.map(([name, count]) => (
                <div key={name} className="flex justify-between gap-2">
                  <dt className="truncate text-muted-foreground">{name}</dt>
                  <dd className="tabular-nums">{count}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        )}
      </Card>
    </section>
  )
}

const NAVIGATION_SERIES = {
  reads: { label: "Reads before the first write", color: "var(--chart-1)" },
} satisfies ChartConfig

/**
 * Whether memory is teaching the agent the workspace: the fewer reads a turn needs before it
 * writes, the better it knows where things are.
 */
function NavigationChart({ trend, by }: { trend: NavigationPoint[]; by: StatsBucketSize }) {
  return (
    <section aria-label="Navigation">
      <Card>
        <CardHeader>
          <CardTitle>Navigation</CardTitle>
          <CardDescription>
            Mean reads before the first write, per {by}, UTC, over the turns that wrote.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ChartContainer config={NAVIGATION_SERIES} className="aspect-auto h-48 w-full">
            <LineChart data={trend} accessibilityLayer>
              <BucketAxes allowDecimals />
              <ChartTooltip
                content={
                  <ChartTooltipContent labelFormatter={(start) => bucketLabel(String(start), by)} />
                }
              />
              <Line
                dataKey={({ reads }: NavigationPoint) =>
                  reads === null ? null : Math.round(reads * 10) / 10
                }
                name="reads"
                stroke="var(--color-reads)"
                strokeWidth={2}
                connectNulls
              />
            </LineChart>
          </ChartContainer>
        </CardContent>
      </Card>
    </section>
  )
}
