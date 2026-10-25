import type { InstanceClient, View } from "@kinby/contract"
import { useEffect, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewRenderer } from "@/components/view-renderer"
import { when } from "@/lib/config-changes"

/**
 * One of an instance's views: its title, when it last changed, and the view. It follows the view
 * while open, showing each snapshot the instance sends after a write.
 */
export function ViewPage({
  client,
  viewId,
}: {
  client: Pick<InstanceClient, "subscribe">
  viewId: string
}) {
  const [{ view, failure }, setShown] = useState<{ view?: View; failure?: unknown }>({})
  useEffect(() => {
    const snapshots = client.subscribe("view.subscribe", { id: viewId })
    let following = true
    const read = async () => {
      try {
        for await (const snapshot of snapshots) {
          if (following) setShown({ view: snapshot })
        }
      } catch (error) {
        if (following) setShown((last) => ({ view: last.view, failure: error }))
      }
    }
    void read()
    return () => {
      following = false
      snapshots.cancel()
    }
  }, [client, viewId])

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
