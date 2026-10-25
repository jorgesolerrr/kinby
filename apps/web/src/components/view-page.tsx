import type { Clock, InstanceClient } from "@kinby/contract"
import { useCallback } from "react"

import { Failure } from "@/components/config-alerts"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewRenderer } from "@/components/view-renderer"
import { useRead } from "@/hooks/use-read"
import { when } from "@/lib/config-changes"

/**
 * One of an instance's views: its title, when it last changed, and the view. It reads on open and
 * when the window regains focus.
 */
export function ViewPage({
  client,
  clock,
  viewId,
}: {
  client: Pick<InstanceClient, "call">
  clock: Clock
  viewId: string
}) {
  const read = useCallback(() => client.call("view.get", { id: viewId }), [client, viewId])
  const { value: view, failure } = useRead(read, clock)
  return (
    <div className="flex max-w-4xl flex-col gap-6 p-6">
      {failure !== undefined && <Failure error={failure} />}
      {view === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <header className="flex flex-col gap-1">
            <h1 className="text-xl font-semibold">{view.title}</h1>
            <p className="text-sm text-muted-foreground">Changed {when(view.updated_at)}</p>
          </header>
          <ViewRenderer view={view} />
        </>
      )}
    </div>
  )
}
