import type { Client, Clock, FactoryRun, FactoryRunDetail } from "@kinby/contract"
import { useCallback, useState } from "react"

import { Failure } from "@/components/config-alerts"
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
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { useRead } from "@/hooks/use-read"
import { needingHumanFirst, standing, workItem } from "@/lib/factory-runs"

/**
 * The instances that hold one of the factory's roles, each with its runs and needs-human count.
 * The runs that need a human come first, with the cause of their stop and what the user can do.
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
            needingHumanFirst(runs).map(async (run) =>
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

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="text-base font-semibold">Instances</h2>
      {failure !== undefined && <Failure error={failure} />}
      {held === undefined
        ? failure === undefined && <Skeleton className="h-24 w-full" />
        : held.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No instance holds a role of {factory} yet.
            </p>
          )}
      {held?.map(({ instance, runs }) => (
        <section
          key={instance.instance_id}
          aria-label={instance.manifest_id}
          className="flex flex-col gap-2"
        >
          <h3 className="flex items-center gap-2 text-sm font-medium">
            {instance.manifest_id}
            <Badge variant="secondary">{instance.factory_role?.name}</Badge>
            <Badge variant={instance.needs_human > 0 ? "destructive" : "outline"}>
              {instance.needs_human} needing a human
            </Badge>
          </h3>
          {runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          ) : (
            <ItemGroup aria-label={`Runs of ${instance.manifest_id}`}>
              {runs.map(({ run, detail }) => (
                <Item
                  key={run.run_id}
                  render={<li />}
                  aria-label={`${workItem(run)}: ${standing(run)}`}
                  variant="outline"
                  size="sm"
                >
                  <ItemContent>
                    <ItemTitle>{workItem(run)}</ItemTitle>
                    <ItemDescription>{standing(run)}</ItemDescription>
                    {detail !== undefined && (
                      <ItemDescription>{detail.attempts.at(-1)?.summary}</ItemDescription>
                    )}
                  </ItemContent>
                  {detail !== undefined && (
                    <StoppedRunActions client={client} detail={detail} onActed={readAgain} />
                  )}
                </Item>
              ))}
            </ItemGroup>
          )}
        </section>
      ))}
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
