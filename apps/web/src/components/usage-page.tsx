import type {
  Client,
  Clock,
  InstanceSummary,
  StatsBucket,
  StatsBucketSize,
  StatsSummaryResult,
  UsageSource,
} from "@kinby/contract"
import { useCallback, useId, useState } from "react"
import { Bar, BarChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Failure } from "@/components/config-alerts"
import { PlansStrip } from "@/components/plans-strip"
import { RangeHeader } from "@/components/range-header"
import { Tile } from "@/components/tile"
import { buttonVariants } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
  runsIn,
  turnsIn,
  type UsageRow,
  usageRows,
} from "@/lib/usage"

type Caller = Pick<Client, "call">

/**
 * The hub's usage across its instances over the last 7, 30, or 90 UTC days. It reads when it
 * opens, when the range changes, when the window regains focus, and on Refresh, and never polls.
 */
export function UsagePage({ client, clock }: { client: Caller; clock: Clock }) {
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
    <div className="flex flex-col gap-4 p-6">
      <RangeHeader title="Usage" range={range} onRange={setRange} onRefresh={readAgain} />
      {value !== undefined && (
        <PlansStrip planUse={value.usage.plan_use} limits={value.usage.limits} />
      )}
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
    </div>
  )
}

/** The instance table first, then the totals, then each instance on a chart. */
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
  // The hub lists every plan, counted or not, so its plans name the columns.
  const sources = usage.subscriptions.map((use) => use.usage_source)
  const counted = rows.flatMap((row) => ("buckets" in row ? [row] : []))
  return (
    <>
      <InstanceTable rows={rows} sources={sources} />
      <Totals usage={usage} />
      <InstanceCharts counted={counted} by={by} starts={starts} />
    </>
  )
}

/**
 * A row per instance the hub summed, each linking to its Stats tab. One the hub left out says why,
 * so a partial total never looks complete.
 */
function InstanceTable({ rows, sources }: { rows: UsageRow[]; sources: UsageSource[] }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h2 id={titleId} className="font-medium">
        Instances
      </h2>
      <Table aria-labelledby={titleId}>
        <TableHeader>
          <TableRow>
            <TableHead>Instance</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            {sources.map((source) => (
              <TableHead key={source} className="text-right">
                {SOURCE_LABELS[source]} runs
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.instance.instance_id}>
              <TableCell>
                <StatsLink instance={row.instance} />
              </TableCell>
              {"notCounted" in row ? (
                <TableCell colSpan={2 + sources.length}>
                  <span className="text-muted-foreground">Not counted: {row.notCounted}</span>
                </TableCell>
              ) : (
                <>
                  <TableCell className="text-right">{turnsIn(row.buckets)}</TableCell>
                  <TableCell className="text-right">{money(costIn(row.buckets))}</TableCell>
                  {sources.map((source) => (
                    <TableCell key={source} className="text-right">
                      {runsIn(row.buckets, source)}
                    </TableCell>
                  ))}
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

/** The counted instances' turns, API cost, and plan runs, and how many of them were counted. */
function Totals({ usage }: { usage: StatsSummaryResult }) {
  const titleId = useId()
  const buckets = Object.values(usage.buckets).flat()
  const counted = Object.keys(usage.buckets).length
  const asked = counted + usage.skipped.length + usage.unreachable.length
  const sum = (count: (bucket: StatsBucket) => number) =>
    buckets.reduce((total, bucket) => total + count(bucket), 0)
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <div>
        <h2 id={titleId} className="font-medium">
          Totals
        </h2>
        <p className="text-sm text-muted-foreground">
          {counted} of {asked} instances counted.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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
      </div>
    </section>
  )
}

/** What the chart can show per instance, each with how its tooltip writes a value. */
const MEASURES = [
  { tab: "API cost", value: (bucket: StatsBucket) => bucket.cost ?? null, format: money },
  {
    tab: "Plan runs",
    value: (bucket: StatsBucket) =>
      bucket.subscriptions.reduce((runs, use) => runs + (use.runs ?? 0), 0),
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
    <section aria-label="Per instance">
      <Card>
        <CardHeader>
          <CardTitle>Per instance</CardTitle>
          <CardDescription>
            {buckets.some((each) => each.length > 0)
              ? `Per ${by}, UTC.`
              : "No counted instance ran in this range."}
          </CardDescription>
        </CardHeader>
        <CardContent>
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
                      <BucketAxes />
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
        </CardContent>
      </Card>
    </section>
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
