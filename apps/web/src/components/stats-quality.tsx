import type {
  LiveFact,
  RecallWeek,
  RunUsage,
  StatsBucketSize,
  StatsGetResult,
} from "@kinby/contract"
import { cn } from "cn"
import { ChevronDownIcon } from "lucide-react"
import type { ReactNode } from "react"
import { Line, LineChart } from "recharts"

import { BucketAxes } from "@/components/bucket-axes"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { RunUsageState } from "@/hooks/use-run-usage"
import { nodePath, openNode } from "@/lib/selection"
import {
  bucketLabel,
  dayLabel,
  median,
  type NavigationPoint,
  navigationTrend,
  shortTokens,
  tokens,
  turnCount,
} from "@/lib/stats"

/** The most-called tools a quality view lists. */
const TOP_TOOLS = 10

/** How well the range's work went: what it called, what it asked, and how it was rated. */
export function Quality({
  stats,
  by,
  starts,
  runUsage,
  instanceId,
}: {
  stats: StatsGetResult
  by: StatsBucketSize
  starts: string[]
  runUsage: RunUsageState
  instanceId: string
}) {
  const { total, recall } = stats
  const trend = navigationTrend(starts, stats.records, by)
  const tools = Object.entries(total.tool_calls)
    .sort(([a, aCalls], [b, bCalls]) => bCalls - aCalls || a.localeCompare(b))
    .slice(0, TOP_TOOLS)
  const memory = total.memory_calls
  const turns = turnCount(total)
  const estimated = total.memory_estimated_turns ?? 0
  const recallCounts: [string, string][] = recall
    ? [
        ["Work that recalled a fact", `${recall.recalled} of ${recall.work}`],
        ["Facts never recalled", `${recall.never_recalled.length} of ${recall.facts}`],
      ]
    : []
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
            ["Tokens per turn", tokens(turns ? Math.round((total.memory_tokens ?? 0) / turns) : 0)],
            ...recallCounts,
          ]}
          note={
            estimated
              ? `${estimated} of ${turns} turns' memory tokens are estimated, not measured.`
              : undefined
          }
        >
          {recall && <RecallWeeks weeks={recall.weeks} />}
          {recall && recall.never_recalled.length > 0 && (
            <NeverRecalled facts={recall.never_recalled} instanceId={instanceId} />
          )}
          <RecallSplit {...runUsage} />
        </Counts>
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
  note,
  children,
}: {
  title: string
  counts: [string, number | string][]
  empty?: string
  note?: string
  children?: ReactNode
}) {
  const description = counts.length === 0 ? empty : note
  return (
    <section aria-label={title}>
      <Card size="sm" className="h-full">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
        {counts.length > 0 && (
          <CardContent>
            <div className="flex flex-col gap-4">
              <dl className="flex flex-col gap-1.5">
                {counts.map(([name, count]) => (
                  <div key={name} className="flex justify-between gap-2">
                    <dt className="truncate text-muted-foreground">{name}</dt>
                    <dd className="tabular-nums">{count}</dd>
                  </div>
                ))}
              </dl>
              {children}
            </div>
          </CardContent>
        )}
      </Card>
    </section>
  )
}

/** Each week's work that recalled a fact, and the facts no work had recalled by its end. */
function RecallWeeks({ weeks }: { weeks: RecallWeek[] }) {
  return (
    <div className="-mx-2 md:-mx-3">
      <Table aria-label="Recall by week" size="fit">
        <TableHeader>
          <TableRow>
            <TableHead>Week of</TableHead>
            <TableHead className="text-right">Work that recalled</TableHead>
            <TableHead className="text-right">Never recalled</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {weeks.map((week) => (
            <TableRow key={week.start}>
              <TableCell>{dayLabel(week.start)}</TableCell>
              <TableCell className="text-right">
                {week.recalled} of {week.work}
              </TableCell>
              <TableCell className="text-right">
                {week.never_recalled} of {week.facts}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** The live facts no work has recalled, each linking to it on the Memory tab. */
function NeverRecalled({ facts, instanceId }: { facts: LiveFact[]; instanceId: string }) {
  return (
    <Collapsible>
      <CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>
        The facts never recalled
        <ChevronDownIcon data-icon="inline-end" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="flex flex-col">
          {facts.map((fact) => (
            <li key={fact.node}>
              <a
                className={cn(
                  buttonVariants({ variant: "link", size: "sm" }),
                  "h-auto min-h-8 wrap-anywhere whitespace-normal",
                )}
                href={nodePath(instanceId, fact.node)}
                onClick={(event) => {
                  event.preventDefault()
                  openNode(instanceId, fact.node)
                }}
              >
                {fact.description}
              </a>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

/**
 * The range's factory runs that recalled a fact next to the ones that did not. It claims no cause,
 * since runs differ in the work they do.
 */
function RecallSplit({ usage, failure }: RunUsageState) {
  if (failure !== undefined) {
    return <p className="text-muted-foreground">The hub did not answer for the range's runs.</p>
  }
  if (usage === undefined) return <Skeleton className="h-20 w-full" />
  if (usage.runs.length === 0) {
    return <p className="text-muted-foreground">No factory run started in this range.</p>
  }
  // A run whose only recall was in a left-out instance would read as one that recalled none.
  const leftOut = usage.skipped.length + usage.unreachable.length + usage.outdated.length
  if (leftOut > 0) {
    return (
      <p className="text-muted-foreground">
        Runs are not split by recall while{" "}
        {leftOut === 1
          ? "1 instance is left out: it is"
          : `${leftOut} instances are left out: each is`}{" "}
        not running, did not answer, or runs an older core.
      </p>
    )
  }
  const groups: [string, RunUsage[]][] = [
    ["Recalled a fact", usage.runs.filter((run) => run.recalled_fact)],
    ["Recalled none", usage.runs.filter((run) => !run.recalled_fact)],
  ]
  // The outer cells pad as far as the counts above are inset, so the columns line up with them.
  return (
    <div className="-mx-2 md:-mx-3">
      <Table size="fit">
        <TableCaption>
          Median token usage and send-backs per run. Runs do different work, so a gap need not come
          from memory.
        </TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Factory runs</TableHead>
            <TableHead className="text-right">Runs</TableHead>
            <TableHead className="text-right">Token usage</TableHead>
            <TableHead className="text-right">Send-backs</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map(([name, runs]) => (
            <TableRow key={name}>
              <TableCell className="whitespace-normal">{name}</TableCell>
              <TableCell className="text-right">{runs.length}</TableCell>
              <TableCell className="text-right">
                {runs.length ? shortTokens(median(runs.map((run) => run.tokens))) : "—"}
              </TableCell>
              <TableCell className="text-right">
                {runs.length ? median(runs.map((run) => run.send_backs)) : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
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
              />
            </LineChart>
          </ChartContainer>
        </CardContent>
      </Card>
    </section>
  )
}
