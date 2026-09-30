import type {
  Clock,
  InstanceClient,
  OriginUse,
  RoutineSummary,
  StatsGetResult,
} from "@kinby/contract"
import { type ReactNode, useEffect, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { buttonVariants } from "@/components/ui/button"
import { Item, ItemContent, ItemGroup, ItemTitle } from "@/components/ui/item"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePace } from "@/hooks/use-pace"
import { retried } from "@/lib/operation"
import { failuresInARow, lastFiring, nextFiring } from "@/lib/routines"
import { openRoutine, routinePath } from "@/lib/selection"
import { money, originName, SOURCE_LABELS } from "@/lib/stats"

type Caller = Pick<InstanceClient, "call">

/**
 * The range's turns by chat and routine, the drill-down in `children`, then the instance's
 * routines. It lists the routines again with every stats read, so both show the same moment.
 */
export function Origins({
  client,
  clock,
  stats,
  instanceId,
  picked,
  onPick,
  children,
}: {
  client: Caller
  clock: Clock
  stats: StatsGetResult
  instanceId: string
  picked: OriginUse | undefined
  onPick: (origin: OriginUse) => void
  children: ReactNode
}) {
  const pacing = usePace(clock)
  const [routines, setRoutines] = useState<RoutineSummary[]>()
  const [failure, setFailure] = useState<unknown>()

  useEffect(() => {
    let current = true
    retried(() => client.call("routine.list", {}), pacing).then(
      (listed) => {
        if (!current) return
        setRoutines(listed.routines)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (current) setFailure(error)
      },
    )
    return () => {
      current = false
    }
  }, [client, pacing, stats])

  return (
    <>
      <OriginTable
        origins={stats.total.origins}
        routines={routines}
        instanceId={instanceId}
        picked={picked}
        onPick={onPick}
      />
      {children}
      <h2 className="font-medium">Routines</h2>
      {failure !== undefined && <Failure error={failure} />}
      {routines !== undefined && <RoutineList routines={routines} />}
    </>
  )
}

/** A row per origin. Clicking one filters the drill-down to its turns. */
function OriginTable({
  origins,
  routines,
  instanceId,
  picked,
  onPick,
}: {
  origins: OriginUse[]
  routines: RoutineSummary[] | undefined
  instanceId: string
  picked: OriginUse | undefined
  onPick: (origin: OriginUse) => void
}) {
  const titleId = useId()
  // The total always has a chat row, so its sources name every column.
  const sources = origins[0]?.runs.map((use) => use.usage_source) ?? []
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <div>
        <h2 id={titleId} className="font-medium">
          Turns by origin
        </h2>
        <p className="text-sm text-muted-foreground">Click a row to list its turns.</p>
      </div>
      <Table aria-labelledby={titleId}>
        <TableHeader>
          <TableRow>
            <TableHead>Origin</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">No work</TableHead>
            <TableHead className="text-right">Failed</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            {sources.map((source) => (
              <TableHead key={source} className="text-right">
                {SOURCE_LABELS[source]} runs
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {origins.map((origin) => (
            <TableRow
              key={origin.routine ?? ""}
              className="cursor-pointer"
              data-state={picked?.routine === origin.routine ? "selected" : undefined}
              tabIndex={0}
              onClick={() => onPick(origin)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onPick(origin)
              }}
            >
              <TableCell>
                <OriginCell origin={origin} routines={routines} instanceId={instanceId} />
              </TableCell>
              <TableCell className="text-right">{origin.turns}</TableCell>
              <TableCell className="text-right">{origin.no_work}</TableCell>
              <TableCell className="text-right">{origin.failed}</TableCell>
              <TableCell className="text-right">{money(origin.cost)}</TableCell>
              {origin.runs.map((use) => (
                <TableCell key={use.usage_source} className="text-right">
                  {use.runs}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

/**
 * Chat, or a routine linking to it in the config panel. The log keeps the name a turn ran under,
 * so a routine `routines` no longer has was removed or renamed, and has nothing to link to.
 */
function OriginCell({
  origin,
  routines,
  instanceId,
}: {
  origin: OriginUse
  routines: RoutineSummary[] | undefined
  instanceId: string
}) {
  const name = origin.routine
  if (name === null || routines === undefined) return originName(origin)
  if (!routines.some((listed) => listed.name === name)) {
    return (
      <span className="flex items-center gap-2">
        {name}
        <Badge variant="secondary">removed or renamed</Badge>
      </span>
    )
  }
  return (
    <a
      className={buttonVariants({ variant: "link", size: "sm" })}
      href={routinePath(instanceId, name)}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        openRoutine(instanceId, name)
      }}
    >
      {name}
    </a>
  )
}

/** Each routine as it stands: how it last fired, its failures in a row, and when it fires next. */
function RoutineList({ routines }: { routines: RoutineSummary[] }) {
  if (routines.length === 0) {
    return <p className="text-sm text-muted-foreground">This instance has no routines.</p>
  }
  return (
    <ItemGroup aria-label="Routines">
      {routines.map((routine) => {
        const failures = routine.failure_count ?? 0
        return (
          <Item key={routine.name} render={<li />} aria-label={routine.name} variant="outline">
            <ItemContent>
              <ItemTitle>{routine.name}</ItemTitle>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                <span>{lastFiring(routine)}</span>
                {failures > 0 ? (
                  <Badge variant="destructive">{failuresInARow(failures)}</Badge>
                ) : (
                  <span>No failures</span>
                )}
                <span>{nextFiring(routine)}</span>
              </div>
            </ItemContent>
          </Item>
        )
      })}
    </ItemGroup>
  )
}
