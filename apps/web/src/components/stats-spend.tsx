import type { StatsBucket, StatsBucketSize, StatsGetResult, UsageSource } from "@kinby/contract"
import { TriangleAlertIcon } from "lucide-react"
import { Bar, BarChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  bucketLabel,
  type BucketPoint,
  bucketPoints,
  cents,
  centsMoney,
  duration,
  money,
  SOURCE_LABELS,
  tokens,
} from "@/lib/stats"

/** Where the range's API cost and plan runs went. */
export function Spend({
  stats,
  by,
  starts,
}: {
  stats: StatsGetResult
  by: StatsBucketSize
  starts: string[]
}) {
  return (
    <>
      <UnpricedNotice models={stats.unpriced_models} />
      <CostChart buckets={stats.buckets} by={by} starts={starts} />
      <Sources stats={stats} />
      <PlanRunsChart buckets={stats.buckets} by={by} starts={starts} />
    </>
  )
}

/** The models the range has no price for, whose turns the API cost leaves out. */
export function UnpricedNotice({ models }: { models: string[] }) {
  return (
    models.length > 0 && (
      <Alert>
        <TriangleAlertIcon />
        <AlertTitle>No price for {new Intl.ListFormat("en").format(models)}</AlertTitle>
        <AlertDescription>
          Their turns add no API cost, so the cost shown is too low.
        </AlertDescription>
      </Alert>
    )
  )
}

const COST_SERIES = {
  cost: { label: "API cost", color: "var(--chart-1)" },
} satisfies ChartConfig

/**
 * The API cost of each bucket. A bucket with no priced turn has no bar, and its tooltip says it
 * was not priced, so it never reads as free. A bucket with no turn at all costs $0.00.
 */
function CostChart({
  buckets,
  by,
  starts,
}: {
  buckets: StatsBucket[]
  by: StatsBucketSize
  starts: string[]
}) {
  const priced = buckets.some((bucket) => bucket.cost != null)
  return (
    <section aria-label="API cost">
      <Card>
        <CardHeader>
          <CardTitle>API cost</CardTitle>
          <CardDescription>
            {priced ? `Per ${by}, UTC.` : "No turn in this range was priced."}
          </CardDescription>
        </CardHeader>
        {priced && (
          <CardContent>
            <ChartContainer config={COST_SERIES} className="aspect-auto h-48 w-full">
              <BarChart data={bucketPoints(starts, buckets)} accessibilityLayer>
                <BucketAxes tickFormatter={centsMoney} />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      labelFormatter={(start) => bucketLabel(String(start), by)}
                      formatter={(_, __, item) => (
                        <CostRow cost={(item.payload as BucketPoint).cost} />
                      )}
                    />
                  }
                />
                <Bar
                  dataKey={(point: BucketPoint) => cents(point.cost) ?? 0}
                  name="cost"
                  fill="var(--color-cost)"
                />
              </BarChart>
            </ChartContainer>
          </CardContent>
        )}
      </Card>
    </section>
  )
}

function CostRow({ cost }: { cost: BucketPoint["cost"] }) {
  return (
    <div className="flex flex-1 items-center justify-between gap-2">
      <span className="text-muted-foreground">{COST_SERIES.cost.label}</span>
      <span className="font-mono font-medium tabular-nums">{money(cost)}</span>
    </div>
  )
}

/** The API's tokens and cost, and each plan's runs, tokens, and time, over the whole range. */
function Sources({ stats: { total } }: { stats: StatsGetResult }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage by source</CardTitle>
        <CardDescription>Plans are counted, never priced.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table aria-label="Usage by source">
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Runs</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="text-right">Time</TableHead>
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>{SOURCE_LABELS.api}</TableCell>
              <TableCell className="text-right">—</TableCell>
              <TableCell className="text-right">
                {tokens(total.input_tokens + total.output_tokens)}
              </TableCell>
              <TableCell className="text-right">—</TableCell>
              <TableCell className="text-right">{money(total.cost)}</TableCell>
            </TableRow>
            {total.subscriptions.map((use) => (
              <TableRow key={use.usage_source}>
                <TableCell>{SOURCE_LABELS[use.usage_source]}</TableCell>
                <TableCell className="text-right">{use.runs ?? 0}</TableCell>
                <TableCell className="text-right">
                  {tokens((use.input_tokens ?? 0) + (use.output_tokens ?? 0))}
                </TableCell>
                <TableCell className="text-right">{duration(use.duration_ms ?? 0)}</TableCell>
                <TableCell className="text-right">not priced</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

const PLAN_SERIES = {
  "claude-subscription": { label: SOURCE_LABELS["claude-subscription"], color: "var(--chart-3)" },
  "chatgpt-subscription": { label: SOURCE_LABELS["chatgpt-subscription"], color: "var(--chart-5)" },
} satisfies ChartConfig

/** How many delegated runs each plan made in each bucket. */
function PlanRunsChart({
  buckets,
  by,
  starts,
}: {
  buckets: StatsBucket[]
  by: StatsBucketSize
  starts: string[]
}) {
  const runs = (point: BucketPoint, source: UsageSource) =>
    point.subscriptions.find((use) => use.usage_source === source)?.runs ?? 0
  const ran = buckets.some((bucket) => bucket.subscriptions.some((use) => (use.runs ?? 0) > 0))
  return (
    <section aria-label="Plan runs">
      <Card>
        <CardHeader>
          <CardTitle>Plan runs</CardTitle>
          <CardDescription>
            {ran ? `Per ${by}, UTC.` : "No plan ran in this range."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ChartContainer config={PLAN_SERIES} className="aspect-auto h-48 w-full">
            <BarChart data={bucketPoints(starts, buckets)} accessibilityLayer>
              <BucketAxes />
              <ChartTooltip
                content={
                  <ChartTooltipContent labelFormatter={(start) => bucketLabel(String(start), by)} />
                }
              />
              <ChartLegend content={<ChartLegendContent nameKey="value" />} />
              {(Object.keys(PLAN_SERIES) as (keyof typeof PLAN_SERIES)[]).map((source) => (
                <Bar
                  key={source}
                  dataKey={(point: BucketPoint) => runs(point, source)}
                  name={source}
                  stackId="runs"
                  fill={`var(--color-${source})`}
                />
              ))}
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>
    </section>
  )
}
