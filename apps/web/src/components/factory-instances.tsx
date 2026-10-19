import type { Client, Clock } from "@kinby/contract"
import { useCallback } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { useRead } from "@/hooks/use-read"
import { standing, workItem } from "@/lib/factory-runs"

/** The instances that hold one of the factory's roles, each with its runs and needs-human count. */
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
      held.map(async (instance) => ({
        instance,
        runs: (
          await client.call("factory.run.list", { factory, instance_id: instance.instance_id })
        ).runs,
      })),
    )
  }, [client, factory])
  const { value: held, failure } = useRead(read, clock)

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="font-medium">Instances</h2>
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
              {runs.map((run) => (
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
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </section>
      ))}
    </section>
  )
}
