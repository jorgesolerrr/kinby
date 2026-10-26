import { CallError, type InstanceClient, type View } from "@kinby/contract"
import { EllipsisIcon, Trash2Icon } from "lucide-react"
import { useEffect, useState } from "react"

import { Failure } from "@/components/config-alerts"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewRenderer } from "@/components/view-renderer"
import { when } from "@/lib/config-changes"
import { landOn } from "@/lib/selection"

/**
 * One of an instance's views: its title, when it last changed, and the view. It follows the view
 * while open, showing each snapshot the instance sends after a write. Its ⋯ menu offers Delete,
 * and a delete that succeeds goes back to the instance. A view that is gone, because its
 * subscription ended or the instance did not find it, shows as deleted.
 */
export function ViewPage({
  client,
  instanceId,
  viewId,
}: {
  client: Pick<InstanceClient, "subscribe" | "call">
  instanceId: string
  viewId: string
}) {
  const [{ view, failure, deleted }, setShown] = useState<{
    view?: View
    failure?: unknown
    deleted?: boolean
  }>({})
  useEffect(() => {
    const snapshots = client.subscribe("view.subscribe", { id: viewId })
    let following = true
    const read = async () => {
      try {
        for await (const snapshot of snapshots) {
          if (following) setShown({ view: snapshot })
        }
        if (following) setShown({ deleted: true })
      } catch (error) {
        if (!following) return
        if (error instanceof CallError && error.code === "NOT_FOUND") setShown({ deleted: true })
        else setShown((last) => ({ view: last.view, failure: error }))
      }
    }
    void read()
    return () => {
      following = false
      snapshots.cancel()
    }
  }, [client, viewId])
  const [asking, setAsking] = useState(false)
  const [refusal, setRefusal] = useState<unknown>()
  const remove = async () => {
    try {
      await client.call("view.delete", { id: viewId })
      landOn(instanceId)
    } catch (error) {
      setRefusal(error)
    }
  }

  if (deleted) {
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
  return (
    <div className="flex max-w-4xl flex-col gap-6 p-6">
      {failure !== undefined && <Failure error={failure} />}
      {view === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <header className="flex items-start gap-2">
            <div className="flex flex-1 flex-col gap-1">
              <h1 className="text-xl font-semibold">{view.title}</h1>
              <p className="text-sm text-muted-foreground">Changed {when(view.updated_at)}</p>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button variant="outline" size="icon" aria-label="More actions" />}
              >
                <EllipsisIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => {
                    setRefusal(undefined)
                    setAsking(true)
                  }}
                >
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <AlertDialog open={asking} onOpenChange={setAsking}>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete {view.title}?</AlertDialogTitle>
                </AlertDialogHeader>
                {refusal !== undefined && <Failure error={refusal} />}
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={() => void remove()}>
                    Delete
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </header>
          <ViewRenderer view={view} />
        </>
      )}
    </div>
  )
}
