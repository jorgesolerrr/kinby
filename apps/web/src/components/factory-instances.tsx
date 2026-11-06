import type {
  Client,
  Clock,
  FactoryRun,
  FactoryRunDetail,
  FactoryRunStatus,
  InstanceSummary,
} from "@kinby/contract"
import { CircleAlertIcon, GitBranchIcon } from "lucide-react"
import { useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { InstanceAvatar } from "@/components/instance-avatar"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { useRead } from "@/hooks/use-read"
import { when } from "@/lib/config-changes"
import { byAttention, finished, STATUSES, standing, workItem } from "@/lib/factory-runs"
import { instanceName } from "@/lib/instances"
import { openTab } from "@/lib/selection"

const BADGES: Record<FactoryRunStatus, "attention" | "success" | "outline" | "secondary"> = {
  "needs-human": "attention",
  running: "success",
  queued: "outline",
  parked: "outline",
  done: "secondary",
  cancelled: "secondary",
}

/** How many finished runs a card shows before the user asks for the rest. */
const FINISHED_SHOWN = 3

/**
 * The instances that hold one of the factory's roles, each in a card with its runs. The runs that
 * need a human come first, with the cause of their stop and what the user can do.
 */
export function FactoryInstances({
  client,
  clock,
  factory,
}: {
  client: Pick<Client, "call">
  clock: Clock
  factory: string
}) {
  const read = useCallback(async () => {
    const { instances } = await client.call("instance.list", {})
    const held = instances.filter((instance) => instance.factory_role?.factory === factory)
    return Promise.all(
      held.map(async (instance) => {
        const { runs } = await client.call("factory.run.list", {
          factory,
          instance_id: instance.instance_id,
        })
        return {
          instance,
          runs: await Promise.all(
            byAttention(runs).map(async (run) =>
              run.status === "needs-human"
                ? { run, detail: await client.call("factory.run.get", { run_id: run.run_id }) }
                : { run },
            ),
          ),
        }
      }),
    )
  }, [client, factory])
  const { value: held, failure, readAgain } = useRead(read, clock)
  const titleId = useId()

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4">
      <h2 id={titleId} className="text-base font-semibold">
        Instances and runs
      </h2>
      {failure !== undefined && <Failure error={failure} />}
      {held === undefined
        ? failure === undefined && <Skeleton className="h-24 w-full" />
        : held.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No instance holds a role of {factory} yet.
            </p>
          )}
      {held?.map(({ instance, runs }) => (
        <InstanceRuns
          key={instance.instance_id}
          client={client}
          instance={instance}
          runs={runs}
          onActed={readAgain}
        />
      ))}
    </section>
  )
}

/** How many of an instance's runs need a human and how many are running. */
function summary(runs: FactoryRun[]): string {
  if (runs.length === 0) return "No runs yet"
  const needing = runs.filter((run) => run.status === "needs-human").length
  const running = runs.filter((run) => run.status === "running").length
  return `${needing} ${needing === 1 ? "needs" : "need"} a human · ${running} running`
}

/**
 * One instance's card: its name, role and Open, then its runs. Of the finished runs only the
 * newest few show until the user asks for the rest.
 */
function InstanceRuns({
  client,
  instance,
  runs,
  onActed,
}: {
  client: Pick<Client, "call">
  instance: InstanceSummary
  runs: { run: FactoryRun; detail?: FactoryRunDetail }[]
  onActed: () => void
}) {
  const [all, setAll] = useState(false)
  const name = instanceName(instance)
  const current = runs.filter(({ run }) => !finished(run)).length
  const shown = all ? runs : runs.slice(0, current + FINISHED_SHOWN)
  const hidden = runs.length - shown.length

  return (
    <section aria-label={name}>
      <ItemGroup variant="outline">
        <li>
          <Item variant="muted" size="xs">
            <ItemMedia>
              <InstanceAvatar avatar={instance.avatar} name={name} size="sm" />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>
                {name}
                <Badge variant="secondary">{instance.factory_role?.name}</Badge>
              </ItemTitle>
              <ItemDescription>{summary(runs.map(({ run }) => run))}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => openTab(instance.instance_id, "overview")}
              >
                Open
              </Button>
            </ItemActions>
          </Item>
        </li>
        {shown.map(({ run, detail }) => (
          <li key={run.run_id} aria-label={`${workItem(run)}: ${standing(run)}`}>
            <Item size="xs">
              <ItemMedia variant="icon">
                {run.status === "needs-human" ? (
                  <CircleAlertIcon className="text-ember" />
                ) : (
                  <GitBranchIcon className="text-muted-foreground" />
                )}
              </ItemMedia>
              {/* On a phone a stopped run's actions wrap under its text. */}
              <ItemContent className="min-w-48">
                <ItemTitle>
                  {workItem(run)}
                  <Badge variant={BADGES[run.status]}>{STATUSES[run.status]}</Badge>
                </ItemTitle>
                <ItemDescription>
                  {[
                    run.step !== null && `at ${run.step}`,
                    when(run.updated_at),
                    run.wakes_at != null && `wakes ${when(run.wakes_at)}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </ItemDescription>
                {detail !== undefined && (
                  <ItemDescription>{detail.attempts.at(-1)?.summary}</ItemDescription>
                )}
              </ItemContent>
              {detail !== undefined && (
                <StoppedRunActions client={client} detail={detail} onActed={onActed} />
              )}
            </Item>
          </li>
        ))}
        {hidden > 0 && (
          <li className="p-1">
            <Button variant="ghost" size="sm" className="w-full" onClick={() => setAll(true)}>
              Show {hidden} more
            </Button>
          </li>
        )}
      </ItemGroup>
    </section>
  )
}

/** Retry a run that needs a human, send it back to an earlier step, or cancel it. */
function StoppedRunActions({
  client,
  detail: { run, earlier_steps },
  onActed,
}: {
  client: Pick<Client, "call">
  detail: FactoryRunDetail
  onActed: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const [cancelling, setCancelling] = useState(false)
  const { run_id } = run
  const act = async (action: () => Promise<FactoryRun>) => {
    setBusy(true)
    setFailure(undefined)
    try {
      await action()
      onActed()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <ItemActions>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => void act(() => client.call("factory.run.retry", { run_id }))}
        >
          Retry
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="outline" size="sm" disabled={busy || earlier_steps.length === 0} />
            }
          >
            Send back
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {earlier_steps.map((step) => (
              <DropdownMenuItem
                key={step}
                onClick={() =>
                  void act(() => client.call("factory.run.send_back", { run_id, step }))
                }
              >
                {step}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <AlertDialog open={cancelling} onOpenChange={setCancelling}>
          <AlertDialogTrigger render={<Button variant="destructive" size="sm" disabled={busy} />}>
            Cancel
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Cancel the run of {workItem(run)}?</AlertDialogTitle>
              <AlertDialogDescription>It ends where it stopped.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  setCancelling(false)
                  void act(() => client.call("factory.run.cancel", { run_id }))
                }}
              >
                Cancel run
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </ItemActions>
      {failure !== undefined && (
        <ItemFooter>
          <Failure error={failure} />
        </ItemFooter>
      )}
    </>
  )
}
