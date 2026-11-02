import type {
  Client,
  Clock,
  InstanceSummary,
  StatsBucket,
  StatsBucketSize,
  StatsSummaryResult,
} from "@kinby/contract"
import { RefreshCwIcon } from "lucide-react"
import { type ReactNode, useCallback, useId, useState } from "react"
import { Bar, BarChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Failure } from "@/components/config-alerts"
import { PlanRows } from "@/components/plans-strip"
import { RangeToggle } from "@/components/range-header"
import { SectionFrame } from "@/components/section-frame"
import { Tile, TileStrip } from "@/components/tile"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useRead } from "@/hooks/use-read"
import { instanceName } from "@/lib/instances"
import { openStats, statsPath } from "@/lib/selection"
import {
  bucketLabel,
  bucketSize,
  bucketStarts,
  cents,
  centsMoney,
  money,
  type Range,
  SOURCE_LABELS,
  statsCommand,
  tokens,
  turnCount,
} from "@/lib/stats"
import {
  type CountedRow,
  costIn,
  type InstancePoint,
  instancePoints,
  planRuns,
  planRunsIn,
  turnsIn,
  type UsageRow,
  usageRows,
} from "@/lib/usage"

type Caller = Pick<Client, "call">

/**
 * The hub's usage across its instances over the last 7, 30, or 90 UTC days. It reads when it
 * opens, when the range changes, when the window regains focus, and on Refresh, and never polls.
 */
export function UsageSection({ client, clock }: { client: Caller; clock: Clock }) {
  const [range, setRange] = useState<Range>(7)
  // The instances are listed with every summary, so the names and the counts show the same moment.
  // The charts' buckets come from the moment the read asked about, so midnight never splits them.
  const read = useCallback(async () => {
    const now = new Date()
    const [usage, listed] = await Promise.all([
      client.call("stats.summary", statsCommand(range, now)),
      client.call("instance.list", {}),
    ])
    const starts = bucketStarts(range, bucketSize(range), now)
    return { usage, instances: listed.instances, starts }
  }, [client, range])
  const { value, failure, readAgain } = useRead(read, clock)
  const by = bucketSize(range)

  return (
    <SectionFrame
      title="Usage"
      lead="What your instances used, counted by the hub."
      actions={
        <>
          <RangeToggle range={range} onRange={setRange} />
          <Button variant="ghost" size="icon-sm" aria-label="Refresh" onClick={readAgain}>
            <RefreshCwIcon />
          </Button>
        </>
      }
    >
      {failure !== undefined && <Failure error={failure} />}
      {value === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <Usage
          usage={value.usage}
          rows={usageRows(value.usage, value.instances)}
          by={by}
          starts={value.starts}
        />
      )}
    </SectionFrame>
  )
}

/** The plans first, then the totals, then each instance on a chart, then the instance table. */
function Usage({
  usage,
  rows,
  by,
  starts,
}: {
  usage: StatsSummaryResult
  rows: UsageRow[]
  by: StatsBucketSize
  starts: string[]
}) {
  const counted = rows.flatMap((row) => ("buckets" in row ? [row] : []))
  return (
    <div className="flex flex-col divide-y">
      <Group title="Plans">
        <PlanRows planUse={usage.plan_use} limits={usage.limits} />
      </Group>
      <Totals usage={usage} />
      <InstanceCharts counted={counted} by={by} starts={starts} />
      <InstanceTable rows={rows} />
    </div>
  )
}

/** One part of the Usage section under its heading, and a note on what it holds. */
function Group({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0">
      <div>
        <h3 id={titleId} className="text-sm font-medium text-muted-foreground">
          {title}
        </h3>
        {note !== undefined && <p className="text-sm text-muted-foreground">{note}</p>}
      </div>
      {children}
    </section>
  )
}

/**
 * A row per instance the hub summed, each linking to its Stats tab. One the hub left out says why,
 * so a partial total never looks complete.
 */
function InstanceTable({ rows }: { rows: UsageRow[] }) {
  return (
    <Group title="Instances">
      <Table aria-label="Instances">
        <TableHeader>
          <TableRow>
            <TableHead>Instance</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            <TableHead className="text-right">Plan runs</TableHead>
            <TableHead className="text-right">Factory runs</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.instance.instance_id}>
              <TableCell>
                <StatsLink instance={row.instance} />
              </TableCell>
              {"notCounted" in row ? (
                <TableCell colSpan={4} className="whitespace-normal">
                  <span className="text-muted-foreground">Not counted: {row.notCounted}</span>
                </TableCell>
              ) : (
                <>
                  <TableCell className="text-right">{turnsIn(row.buckets)}</TableCell>
                  <TableCell className="text-right">{money(costIn(row.buckets))}</TableCell>
                  <TableCell className="text-right">{planRunsIn(row.buckets)}</TableCell>
                  <TableCell className="text-right">{row.runs.runs}</TableCell>
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Group>
  )
}

/**
 * The counted instances' turns, API cost, plan runs, and factory runs, and how many of them were
 * counted.
 */
function Totals({ usage }: { usage: StatsSummaryResult }) {
  const buckets = Object.values(usage.buckets).flat()
  const counted = Object.keys(usage.buckets).length
  const asked = counted + usage.skipped.length + usage.unreachable.length + usage.outdated.length
  const sum = (count: (bucket: StatsBucket) => number) =>
    buckets.reduce((total, bucket) => total + count(bucket), 0)
  return (
    <Group title="Totals" note={`${counted} of ${asked} instances counted.`}>
      <TileStrip className="grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          title="Turns"
          value={String(turnsIn(buckets))}
          note={`${sum((bucket) => bucket.failed)} failed · ${sum((bucket) => bucket.interrupted)} interrupted`}
        />
        <Tile
          title="API cost"
          value={money(usage.api.cost)}
          note={`${tokens(usage.api.input_tokens)} in · ${tokens(usage.api.output_tokens)} out`}
        />
        <Tile
          title="Plan runs"
          value={String(usage.subscriptions.reduce((runs, use) => runs + (use.runs ?? 0), 0))}
          note={usage.subscriptions
            .map((use) => `${SOURCE_LABELS[use.usage_source]} ${use.runs ?? 0}`)
            .join(" · ")}
        />
        <Tile
          title="Factory runs"
          value={String(usage.run_total.runs)}
          note={`${tokens(usage.run_total.tokens)} tokens · ${money(usage.run_total.cost)}`}
        />
      </TileStrip>
    </Group>
  )
}

/** What the chart can show per instance, each with how its tooltip and axis write a value. */
const MEASURES = [
  { tab: "API cost", value: (bucket: StatsBucket) => cents(bucket.cost), format: centsMoney },
  {
    tab: "Plan runs",
    value: planRuns,
    format: String,
  },
  { tab: "Turns", value: turnCount, format: String },
] satisfies {
  tab: string
  value: (bucket: StatsBucket) => number | null
  format: (value: number | null) => string
}[]

/** Each counted instance's API cost, plan runs, or turns in each bucket, stacked. */
function InstanceCharts({
  counted,
  by,
  starts,
}: {
  counted: CountedRow[]
  by: StatsBucketSize
  starts: string[]
}) {
  const series = counted.map((_, index) => `instance-${index}`)
  const config: ChartConfig = Object.fromEntries(
    counted.map(({ instance }, index) => [
      series[index],
      { label: instanceName(instance), color: CHART_COLORS[index % CHART_COLORS.length] },
    ]),
  )
  const buckets = counted.map((row) => row.buckets)
  return (
    <Group
      title="Per instance"
      note={
        buckets.some((each) => each.length > 0)
          ? `Per ${by}, UTC.`
          : "No counted instance ran in this range."
      }
    >
      <Tabs defaultValue={MEASURES[0].tab}>
        <TabsList variant="line">
          {MEASURES.map(({ tab }) => (
            <TabsTrigger key={tab} value={tab}>
              {tab}
            </TabsTrigger>
          ))}
        </TabsList>
        {MEASURES.map(({ tab, value, format }) => {
          const points = instancePoints(starts, buckets, value)
          return (
            <TabsContent key={tab} value={tab}>
              <ChartContainer config={config} className="aspect-auto h-64 w-full">
                <BarChart data={points} accessibilityLayer>
                  <BucketAxes tickFormatter={format} />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(start) => bucketLabel(String(start), by)}
                        formatter={(_, name, item) => {
                          const index = series.indexOf(String(name))
                          return (
                            <ValueRow
                              label={instanceName(counted[index].instance)}
                              value={format((item.payload as InstancePoint).values[index])}
                            />
                          )
                        }}
                      />
                    }
                  />
                  <ChartLegend content={<ChartLegendContent nameKey="value" />} />
                  {series.map((key, index) => (
                    <Bar
                      key={key}
                      dataKey={(point: InstancePoint) => point.values[index] ?? 0}
                      name={key}
                      stackId="instances"
                      fill={`var(--color-${key})`}
                    />
                  ))}
                </BarChart>
              </ChartContainer>
            </TabsContent>
          )
        })}
      </Tabs>
    </Group>
  )
}

const CHART_COLORS = [1, 2, 3, 4, 5].map((index) => `var(--chart-${index})`)

/** A tooltip row: an instance and its value in the bucket. */
function ValueRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-1 items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono font-medium tabular-nums">{value}</span>
    </div>
  )
}

function StatsLink({ instance }: { instance: InstanceSummary }) {
  return (
    <a
      className={buttonVariants({ variant: "link", size: "sm" })}
      href={statsPath(instance.instance_id)}
      onClick={(event) => {
        event.preventDefault()
        openStats(instance.instance_id)
      }}
    >
      {instanceName(instance)}
    </a>
  )
}
