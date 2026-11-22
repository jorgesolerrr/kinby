import type { InstanceClient, View } from "@kinby/contract"
import { HistoryIcon, Trash2Icon } from "lucide-react"

import { Failure } from "@/components/config-alerts"
import { PageColumn, PageGutters } from "@/components/page-column"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewFrame } from "@/components/view-frame"
import { type FollowedView, useView } from "@/hooks/use-view"
import { when } from "@/lib/config-changes"

/**
 * The view open on its instance's screen: when it last changed, in the page column, and the view,
 * in the column's gutters at the screen's full width. It follows the view while open, showing each
 * snapshot the instance sends after a write. A view that is gone, because its subscription ended
 * or the instance did not find it, shows as deleted.
 */
export function ViewPage({
  client,
  viewId,
}: {
  client: Pick<InstanceClient, "call" | "subscribe">
  viewId: string
}) {
  const { view, failure, deleted } = useView(client, viewId)
  if (deleted) return <Deleted />
  return (
    <div className="flex flex-col gap-4">
      <PageColumn className="flex flex-col gap-4">
        {failure !== undefined && <Failure error={failure} />}
        {view === undefined ? (
          failure === undefined && <Skeleton className="h-72 w-full" />
        ) : (
          <p className="text-sm text-muted-foreground">Changed {when(view.updated_at)}</p>
        )}
      </PageColumn>
      {view !== undefined && (
        <PageGutters className="flex flex-col gap-4">
          <Drawn client={client} view={view} />
        </PageGutters>
      )}
    </div>
  )
}

/** A view followed in the pane beside a thread: the view, scrolling inside the pane. */
export function ViewPane({
  client,
  followed: { view, failure, deleted },
}: {
  client: Pick<InstanceClient, "call">
  followed: FollowedView
}) {
  if (deleted) return <Deleted />
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 *:shrink-0">
      {failure !== undefined && <Failure error={failure} />}
      {view === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <Drawn client={client} view={view} />
      )}
    </div>
  )
}

/**
 * The view's page, which saves the errors it raises to the view, or for a view written for the
 * view catalogue, how to get one.
 */
function Drawn({ client, view }: { client: Pick<InstanceClient, "call">; view: View }) {
  if (view.page === null) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <HistoryIcon />
          </EmptyMedia>
          <EmptyTitle>Written for an older kinby</EmptyTitle>
          <EmptyDescription>
            This kinby draws views another way. Ask the instance to write it again.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  return (
    <ViewFrame
      title={view.title}
      page={view.page}
      data={view.data}
      // A failed save leaves the errors shown, and only the agent misses them.
      onErrors={(errors) => client.call("view.errors.set", { id: view.id, errors }).catch(() => {})}
    />
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
