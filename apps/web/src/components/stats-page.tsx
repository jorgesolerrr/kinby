import type {
  Clock,
  InstanceClient,
  OriginUse,
  StatsBucket,
  StatsBucketSize,
  StatsGetResult,
  StatsSummary,
  TurnMetrics,
} from "@kinby/contract"
import { type ReactNode, useCallback, useId, useState } from "react"
import { Bar, BarChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Failure } from "@/components/config-alerts"
import { PlansStrip } from "@/components/plans-strip"
import { RangeHeader } from "@/components/range-header"
import { Origins } from "@/components/stats-origin"
import { Quality } from "@/components/stats-quality"
import { Spend, UnpricedNotice } from "@/components/stats-spend"
import { Tile } from "@/components/tile"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button, buttonVariants } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
import { selectThread, threadPath } from "@/lib/selection"
import {
  bucketLabel,
  bucketSize,
  dayLabel,
  type Drill,
  drilledTurns,
  money,
  originLabel,
  originName,
  type Range,
  SOURCE_LABELS,
  statsCommand,
  turnCount,
} from "@/lib/stats"
import { TriangleAlertIcon, XIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/**
 * An instance's stats over the last 7, 30, or 90 UTC days. It reads when it opens, when the range
 * changes, when the window regains focus, and on Refresh, and never polls.
 */
export function StatsPage({
  client,
  clock,
  instanceId,
}: {
  client: Caller
  clock: Clock
  instanceId: string
}) {
  const [range, setRange] = useState<Range>(7)
  const read = useCallback(
    () => client.call("stats.get", statsCommand(range, new Date())),
    [client, range],
  )
  const { value: stats, failure, readAgain } = useRead(read, clock)
  const [drill, setDrill] = useState<Drill>({})
  const by = bucketSize(range)
  // A second click on the picked row drops the filter.
  const pickOrigin = (origin: OriginUse) =>
    setDrill(({ bucket, origin: picked }) => ({
      bucket,
      origin: picked?.routine === origin.routine ? undefined : origin,
    }))
  const drillDown = (stats: StatsGetResult) =>
    (drill.bucket !== undefined || drill.origin !== undefined) && (
      <DrilledTurns
        title={drillTitle(drill, by)}
        turns={drilledTurns(stats.records, drill, by)}
        instanceId={instanceId}
        onClose={() => setDrill({})}
      />
    )

  return (
    <div className="flex flex-col gap-4 p-6">
      <RangeHeader
        title="Stats"
        range={range}
        onRange={(next) => {
          setRange(next)
          // The range's buckets start on other days. An origin can have turns in any range.
          setDrill(({ origin }) => ({ origin }))
        }}
        onRefresh={readAgain}
      />
      {stats !== undefined && <PlansStrip planUse={stats.plan_use} limits={stats.limits} />}
      {failure !== undefined && <Failure error={failure} />}
      <Tabs defaultValue="overview">
        <TabsList variant="line">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="spend">Spend</TabsTrigger>
          <TabsTrigger value="origin">Origin</TabsTrigger>
          <TabsTrigger value="quality">Quality</TabsTrigger>
        </TabsList>
        {stats === undefined ? (
          failure === undefined && <Skeleton className="mt-4 h-72 w-full" />
        ) : (
          <>
            <TabsContent value="overview">
              <div className="flex flex-col gap-4 pt-4">
                <Overview
                  stats={stats}
                  by={by}
                  onBucket={(bucket) => setDrill(({ origin }) => ({ bucket, origin }))}
                >
                  {drillDown(stats)}
                </Overview>
              </div>
            </TabsContent>
            <TabsContent value="spend">
              <div className="flex flex-col gap-4 pt-4">
                <Spend stats={stats} by={by} />
              </div>
            </TabsContent>
            <TabsContent value="origin">
              <div className="flex flex-col gap-4 pt-4">
                <Origins
                  client={client}
                  clock={clock}
                  stats={stats}
                  instanceId={instanceId}
                  picked={drill.origin}
                  onPick={pickOrigin}
                >
                  {drillDown(stats)}
                </Origins>
              </div>
            </TabsContent>
            <TabsContent value="quality">
              <div className="flex flex-col gap-4 pt-4">
                <Quality stats={stats} by={by} />
              </div>
            </TabsContent>
          </>
        )}
      </Tabs>
    </div>
  )
}

/** The notices, the tiles, and the turns chart, then the drill-down in `children`. */
function Overview({
  stats,
  by,
  onBucket,
  children,
}: {
  stats: StatsGetResult
  by: StatsBucketSize
  onBucket: (start: string) => void
  children: ReactNode
}) {
  return (
    <>
      <Notices unpricedModels={stats.unpriced_models} mismatches={stats.warnings?.length ?? 0} />
      <Tiles total={stats.total} />
      <TurnsChart buckets={stats.buckets} by={by} onBucket={onBucket} />
      {children}
    </>
  )
}

/** What a drill lists, as "inbox turns closed on Sep 28" or "Turns closed in this range". */
function drillTitle({ bucket, origin }: Drill, by: StatsBucketSize): string {
  const whose = origin === undefined ? "Turns" : `${originName(origin)} turns`
  if (bucket === undefined) return `${whose} closed in this range`
  if (by === "week") return `${whose} closed in the week of ${dayLabel(bucket)}`
  return `${whose} closed on ${dayLabel(bucket)}`
}

/** Why a cost or a token count in the range may be off. */
function Notices({ unpricedModels, mismatches }: { unpricedModels: string[]; mismatches: number }) {
  return (
    <>
      <UnpricedNotice models={unpricedModels} />
      {mismatches > 0 && (
        <Alert>
          <TriangleAlertIcon />
          <AlertTitle>
            {mismatches === 1 ? "1 turn's" : `${mismatches} turns'`} model calls don't add up
          </AlertTitle>
          <AlertDescription>
            The tokens of the model calls differ from the tokens the turn closed with.
          </AlertDescription>
        </Alert>
      )}
    </>
  )
}

function Tiles({ total }: { total: StatsSummary }) {
  const rated = total.good_ratings + total.bad_ratings > 0
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        title="Turns"
        value={String(turnCount(total))}
        note={`${total.failed} failed · ${total.interrupted} interrupted`}
      />
      <Tile
        title="API cost"
        value={money(total.cost)}
        note={`${total.input_tokens.toLocaleString("en")} in · ${total.output_tokens.toLocaleString("en")} out`}
      />
      <Tile
        title="Plan runs"
        value={String(total.subscriptions.reduce((runs, use) => runs + (use.runs ?? 0), 0))}
        note={total.subscriptions
          .map((use) => `${SOURCE_LABELS[use.usage_source]} ${use.runs ?? 0}`)
          .join(" · ")}
      />
      <Tile
        title="Ratings"
        value={rated ? `${total.good_ratings} good` : "—"}
        note={rated ? `${total.bad_ratings} bad` : "No ratings"}
      />
    </div>
  )
}

const TURN_SERIES = {
  completed: { label: "Completed", color: "var(--chart-2)" },
  failed: { label: "Failed", color: "var(--destructive)" },
  interrupted: { label: "Interrupted", color: "var(--chart-4)" },
} satisfies ChartConfig

/** The turns that closed in each bucket, stacked by how they closed. A bar opens its turns. */
function TurnsChart({
  buckets,
  by,
  onBucket,
}: {
  buckets: StatsBucket[]
  by: StatsBucketSize
  onBucket: (start: string) => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Turns</CardTitle>
        <CardDescription>
          {buckets.length === 0
            ? "No turn closed in this range."
            : `Per ${by}, UTC. Click a bar to list its turns.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ChartContainer config={TURN_SERIES} className="aspect-auto h-64 w-full">
          <BarChart data={buckets} accessibilityLayer>
            <BucketAxes />
            <ChartTooltip
              content={
                <ChartTooltipContent labelFormatter={(start) => bucketLabel(String(start), by)} />
              }
            />
            <ChartLegend content={<ChartLegendContent />} />
            {Object.keys(TURN_SERIES).map((series) => (
              <Bar
                key={series}
                dataKey={series}
                stackId="turns"
                fill={`var(--color-${series})`}
                className="cursor-pointer"
                onClick={(_, index) => onBucket(buckets[index].start)}
              />
            ))}
          </BarChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}

const VERDICTS = { good: "Good", bad: "Bad" }

/** A drill's turns, each with who started it, how it ended, and a link to its thread. */
function DrilledTurns({
  title,
  turns,
  instanceId,
  onClose,
}: {
  title: string
  turns: TurnMetrics[]
  instanceId: string
  onClose: () => void
}) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id={titleId} className="font-medium">
          {title}
        </h2>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      <Table aria-labelledby={titleId}>
        <TableHeader>
          <TableRow>
            <TableHead>Origin</TableHead>
            <TableHead>Outcome</TableHead>
            <TableHead>Rating</TableHead>
            <TableHead>Delegated runs</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            <TableHead>
              <span className="sr-only">Link</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {turns.map((turn) => (
            <TableRow key={`${turn.thread_id} ${turn.turn_id}`}>
              <TableCell>{originLabel(turn.origin)}</TableCell>
              <TableCell>
                <Badge variant={turn.closing_kind === "completed" ? "outline" : "destructive"}>
                  {turn.closing_kind}
                </Badge>
              </TableCell>
              <TableCell>{turn.rating === null ? "—" : VERDICTS[turn.rating.verdict]}</TableCell>
              <TableCell>
                {turn.delegated_runs?.length
                  ? turn.delegated_runs.map((reported) => reported.run.client).join(" · ")
                  : "—"}
              </TableCell>
              <TableCell className="text-right">{money(turn.cost)}</TableCell>
              <TableCell className="text-right">
                <a
                  className={buttonVariants({ variant: "link", size: "sm" })}
                  href={threadPath(instanceId, turn.thread_id)}
                  onClick={(event) => {
                    event.preventDefault()
                    selectThread(instanceId, turn.thread_id)
                  }}
                >
                  Open in chat
                </a>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}
