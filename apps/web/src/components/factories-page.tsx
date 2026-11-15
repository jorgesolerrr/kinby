import type { Client, Clock, InstanceSummary, Line, Workbench } from "@kinby/contract"
import { cn } from "cn"
import type * as React from "react"
import { useCallback, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { InstanceAvatar } from "@/components/instance-avatar"
import { PageColumn } from "@/components/page-column"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useRead } from "@/hooks/use-read"
import { STATUS_BADGES, STATUSES, workItem } from "@/lib/factory-runs"
import { instanceName } from "@/lib/instances"
import {
  dailyBudget,
  type LineRun,
  type LineRuns,
  lineName,
  readFloor,
  unfinished,
} from "@/lib/lines"
import { libraryPath, linePath, navigate } from "@/lib/selection"

/** The board's columns, each holding the unfinished runs it names. */
const COLUMNS: { title: string; holds: (held: LineRun) => boolean }[] = [
  { title: "Needs you", holds: ({ needsYou }) => needsYou },
  { title: "Running", holds: ({ run, needsYou }) => !needsYou && run.status === "running" },
  {
    title: "Waiting",
    holds: ({ run, needsYou }) => !needsYou && (run.status === "parked" || run.status === "queued"),
  },
]

/**
 * The Factories area: a board of the lines' unfinished runs, in the columns Needs you, Running and
 * Waiting, over a card per factory with its steps and a table of its lines. A row of filters
 * narrows both to one factory. A run's card and a line's row open the line's page. `instances`
 * name the lines' owners.
 */
export function FactoriesPage({
  caller,
  clock,
  instances,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instances: InstanceSummary[]
}) {
  const read = useCallback(() => readFloor(caller), [caller])
  const { value: floor, failure } = useRead(read, clock)
  const [only, setOnly] = useState<string>()
  const shown = (factory: string) => only === undefined || only === factory
  const lines = floor?.lines.filter(({ line }) => shown(line.factory)) ?? []
  const runs = lines.flatMap(({ line, runs }) =>
    unfinished(runs).map((held) => ({ ...held, line })),
  )

  return (
    <PageColumn className="flex flex-col gap-6 py-6">
      <h1 className="font-heading text-2xl font-semibold">Factories</h1>
      {failure !== undefined && <Failure error={failure} />}
      {floor === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Filter pressed={only === undefined} onClick={() => setOnly(undefined)}>
              All
            </Filter>
            {floor.factories.map(({ name }) => (
              <Filter key={name} pressed={only === name} onClick={() => setOnly(name)}>
                <span className="font-mono">{name}</span>
              </Filter>
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {COLUMNS.map(({ title, holds }) => {
              const held = runs.filter(holds)
              return (
                <section
                  key={title}
                  aria-label={title}
                  className="flex flex-col gap-2 rounded-xl bg-muted/50 p-3"
                >
                  <h2 className="flex items-center justify-between text-sm font-medium">
                    {title}
                    <span className="text-muted-foreground">{held.length}</span>
                  </h2>
                  {held.length === 0 && <p className="text-sm text-muted-foreground">None</p>}
                  {held.map(({ run, needsYou, line }) => {
                    const name = lineName(line, floor.workbenches)
                    return (
                      <PageLink
                        key={run.run_id}
                        href={linePath(line.line_id)}
                        aria-label={`${workItem(run)} on ${name}`}
                        className="flex flex-col gap-1 rounded-lg border bg-background p-3 text-sm hover:bg-muted"
                      >
                        <span className="font-medium">{workItem(run)}</span>
                        <span className="text-xs text-muted-foreground">{name}</span>
                        <span className="flex items-center gap-2">
                          <RunBadge run={run} needsYou={needsYou} />
                          {run.step !== null && (
                            <span className="text-xs text-muted-foreground">{run.step}</span>
                          )}
                        </span>
                      </PageLink>
                    )
                  })}
                </section>
              )
            })}
          </div>
          {floor.factories
            .filter(({ name }) => shown(name))
            .map((factory) => (
              <section key={factory.name} aria-label={factory.name}>
                <Card>
                  <CardHeader>
                    <CardTitle>
                      <span className="font-mono">{factory.name}</span>
                    </CardTitle>
                    <CardDescription>
                      {factory.steps.map(({ id }) => id).join(" → ")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {factory.older ? (
                      <p className="text-muted-foreground">
                        Its steps run in the instances that hold its roles.{" "}
                        <PageLink href={libraryPath("factory")} className="underline">
                          See them in the Library
                        </PageLink>
                      </p>
                    ) : (
                      <FactoryLines
                        lines={floor.lines.filter(({ line }) => line.factory === factory.name)}
                        workbenches={floor.workbenches}
                        instances={instances}
                      />
                    )}
                  </CardContent>
                </Card>
              </section>
            ))}
        </>
      )}
    </PageColumn>
  )
}

/** A factory's lines as a table: each one's workbench, owner, unfinished runs and daily budget. */
function FactoryLines({
  lines,
  workbenches,
  instances,
}: {
  lines: LineRuns[]
  workbenches: Workbench[]
  instances: InstanceSummary[]
}) {
  if (lines.length === 0) return <p className="text-muted-foreground">No lines yet.</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Workbench</TableHead>
          <TableHead>Owner</TableHead>
          <TableHead>Unfinished runs</TableHead>
          <TableHead className="text-right">Daily budget</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map(({ line, runs }) => {
          const workbench = workbenches.find((held) => held.workbench_id === line.workbench_id)
          const open = unfinished(runs)
          return (
            <TableRow key={line.line_id}>
              <TableCell>
                <PageLink href={linePath(line.line_id)} className="font-medium hover:underline">
                  {workbench?.name ?? line.workbench_id}
                </PageLink>
              </TableCell>
              <TableCell>
                <Owner line={line} instances={instances} />
              </TableCell>
              <TableCell>
                <span className="flex flex-wrap gap-1">
                  {open.length === 0 && <span className="text-muted-foreground">None</span>}
                  {open.map(({ run, needsYou }) => (
                    <RunBadge key={run.run_id} run={run} needsYou={needsYou} />
                  ))}
                </span>
              </TableCell>
              <TableCell className="text-right">
                <span className="tabular-nums">{dailyBudget(line)}</span>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Filter({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      size="sm"
      variant={pressed ? "secondary" : "ghost"}
      aria-pressed={pressed}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

/** A run's status, or "Needs you" for a parked one that waits on the user. */
export function RunBadge({ run, needsYou }: LineRun) {
  return (
    <Badge variant={needsYou ? "attention" : STATUS_BADGES[run.status]}>
      {needsYou && run.status === "parked" ? "Needs you" : STATUSES[run.status]}
    </Badge>
  )
}

/** The line's owner, by its avatar and name. */
export function Owner({ line, instances }: { line: Line; instances: InstanceSummary[] }) {
  const owner = instances.find((instance) => instance.instance_id === line.owner_id)
  if (owner === undefined) return <span>{line.owner_id}</span>
  const name = instanceName(owner)
  return (
    <span className="flex items-center gap-2">
      <InstanceAvatar avatar={owner.avatar} name={name} size="sm" />
      {name}
    </span>
  )
}

/** A link to a page of the app, opened in place unless the user asks for another tab. */
export function PageLink({
  href,
  className,
  children,
  ...props
}: React.ComponentProps<"a"> & { href: string }) {
  return (
    <a
      href={href}
      className={cn("underline-offset-4", className)}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
          return
        event.preventDefault()
        navigate(href)
      }}
      {...props}
    >
      {children}
    </a>
  )
}
