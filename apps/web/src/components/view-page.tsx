import type { InstanceClient } from "@kinby/contract"
import { Trash2Icon } from "lucide-react"

import { Failure } from "@/components/config-alerts"
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewRenderer } from "@/components/view-renderer"
import { type FollowedView, useView } from "@/hooks/use-view"
import { when } from "@/lib/config-changes"

/**
 * The view open on its instance's screen: when it last changed, and the view. It follows the view
 * while open, showing each snapshot the instance sends after a write. A view that is gone, because
 * its subscription ended or the instance did not find it, shows as deleted.
 */
export function ViewPage({
  client,
  viewId,
}: {
  client: Pick<InstanceClient, "subscribe">
  viewId: string
}) {
  const { view, failure, deleted } = useView(client, viewId)
  if (deleted) return <Deleted />
  return (
    <div className="flex flex-col gap-4">
      {failure !== undefined && <Failure error={failure} />}
      {view === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Changed {when(view.updated_at)}</p>
          <ViewRenderer view={view} />
        </>
      )}
    </div>
  )
}

/** A view followed in the pane beside a thread: the view, scrolling inside the pane. */
export function ViewPane({ followed: { view, failure, deleted } }: { followed: FollowedView }) {
  if (deleted) return <Deleted />
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 *:shrink-0">
      {failure !== undefined && <Failure error={failure} />}
      {view === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <ViewRenderer view={view} />
      )}
    </div>
  )
}

function Deleted() {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Trash2Icon />
        </EmptyMedia>
        <EmptyTitle>This view was deleted</EmptyTitle>
      </EmptyHeader>
    </Empty>
  )
}
