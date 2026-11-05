import type { RunUsage, RunWeek, StatsRunsResult } from "@kinby/contract"
import { TriangleAlertIcon } from "lucide-react"
import { useId } from "react"

import { Failure } from "@/components/config-alerts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { RunUsageState } from "@/hooks/use-run-usage"
import { standing, workItem } from "@/lib/factory-runs"
import { dayLabel, duration, money, SOURCE_LABELS, tokens } from "@/lib/stats"

/** The factory runs the instance handed in during the range, each with its run usage, and the weekly medians. */
export function Runs({ usage, failure }: RunUsageState) {
  return (
    <>
      {failure !== undefined && <Failure error={failure} />}
      {usage === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : (
        <Usage usage={usage} />
      )}
    </>
  )
}

function Usage({ usage }: { usage: StatsRunsResult }) {
  const leftOut = usage.skipped.length + usage.unreachable.length + usage.outdated.length
  return (
    <>
      {leftOut > 0 && (
        <Alert>
          <TriangleAlertIcon />
          <AlertTitle>
            Tokens leave out {leftOut === 1 ? "1 instance" : `${leftOut} instances`}
          </AlertTitle>
          <AlertDescription>
            An instance that is not running, did not answer, or runs an older core counts none.
          </AlertDescription>
        </Alert>
      )}
      {usage.runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No factory run started in this range.</p>
      ) : (
        <>
          <WeekTable weeks={usage.weeks} />
          <RunTable runs={usage.runs} />
        </>
      )}
    </>
  )
}

function lasted(seconds: number | null): string {
  return seconds === null ? "—" : duration(seconds * 1000)
}

/** The median run of each UTC week, so a trend shows across weeks. */
function WeekTable({ weeks }: { weeks: RunWeek[] }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h3 id={titleId} className="font-semibold">
        Weekly medians
      </h3>
      <Table aria-labelledby={titleId} size="fit">
        <TableHeader>
          <TableRow>
            <TableHead>Week of</TableHead>
            <TableHead className="text-right">Runs</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
            <TableHead className="hidden text-right md:table-cell">Attempts</TableHead>
            <TableHead className="hidden text-right md:table-cell">Duration</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {weeks.map((week) => (
            <TableRow key={week.start}>
              <TableCell>{dayLabel(week.start)}</TableCell>
              <TableCell className="text-right">{week.runs}</TableCell>
              <TableCell className="text-right">{tokens(Math.round(week.tokens))}</TableCell>
              <TableCell className="hidden text-right md:table-cell">{week.attempts}</TableCell>
              <TableCell className="hidden text-right md:table-cell">
                {lasted(week.duration_seconds)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

/** A row per run: what it cost and what it did again. */
function RunTable({ runs }: { runs: RunUsage[] }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h3 id={titleId} className="font-semibold">
        Each run
      </h3>
      <Table aria-labelledby={titleId} size="fit">
        <TableHeader>
          <TableRow>
            <TableHead>Work item</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            <TableHead className="hidden md:table-cell">Plan runs</TableHead>
            <TableHead className="hidden md:table-cell">Attempts</TableHead>
            <TableHead className="hidden text-right md:table-cell">Retries</TableHead>
            <TableHead className="hidden text-right md:table-cell">Send-backs</TableHead>
            <TableHead className="hidden text-right md:table-cell">Duration</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((usage) => (
            <TableRow key={usage.run.run_id}>
              <TableCell className="whitespace-normal">{workItem(usage.run)}</TableCell>
              <TableCell className="whitespace-normal">{standing(usage.run)}</TableCell>
              <TableCell className="text-right">{tokens(usage.tokens)}</TableCell>
              <TableCell className="text-right">{money(usage.api.cost)}</TableCell>
              <TableCell className="hidden md:table-cell">{planRuns(usage)}</TableCell>
              <TableCell className="hidden md:table-cell">
                {usage.attempts.map(({ step, attempts }) => `${step} ${attempts}`).join(" · ")}
              </TableCell>
              <TableCell className="hidden text-right md:table-cell">{usage.retries}</TableCell>
              <TableCell className="hidden text-right md:table-cell">{usage.send_backs}</TableCell>
              <TableCell className="hidden text-right md:table-cell">
                {lasted(usage.duration_seconds)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

/** The delegated runs each plan paid for, counted and never priced. */
function planRuns({ subscriptions }: RunUsage): string {
  const used = subscriptions.filter((use) => (use.runs ?? 0) > 0)
  return used.length === 0
    ? "—"
    : used.map((use) => `${SOURCE_LABELS[use.usage_source]} ${use.runs}`).join(" · ")
}
