import type { Clock, InstanceClient, ViewSummary } from "@kinby/contract"
import { EllipsisIcon } from "lucide-react"
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react"

import { Composer } from "@/components/composer"
import { Failure } from "@/components/config-alerts"
import { PageColumn } from "@/components/page-column"
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
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ViewPage } from "@/components/view-page"
import { POLL_INTERVAL_MS, usePolled } from "@/hooks/use-polled"
import {
  homePath,
  landAt,
  openView,
  selectThread,
  useScreenOpen,
  useViewOpen,
  viewPath,
} from "@/lib/selection"

/** The view last open on an instance's screen in this browser, by instance. */
const LAST_OPEN = "kinby-screen-"

/**
 * An instance's screen: its views as tabs, the most recently changed first, listed again as
 * `usePolled` says, over the view the URL opens. With none named, it opens on the
 * view last open for the instance in this browser, or else the most recently changed one, and an
 * instance with no views lands on the home screen sent to it. The open tab's menu offers Delete,
 * and a delete that succeeds shows the next view, or the one before it, or the home screen. Under
 * the open view, a composer starts a thread as the home screen's does, and opens it with the view
 * in the pane beside it.
 */
export function InstanceScreen({
  client,
  clock,
  instanceId,
  name,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  clock: Clock
  instanceId: string
  name: string
}) {
  const screenOpen = useScreenOpen()
  const viewId = useViewOpen()
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const list = useCallback(async () => (await client.call("view.list", {})).views, [client])
  const listSkills = useCallback(async () => (await client.call("skill.list", {})).skills, [client])
  const [listed, listAgain] = usePolled(list, clock, connected, POLL_INTERVAL_MS)
  const views = useMemo(
    () => listed?.toSorted((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)),
    [listed],
  )
  useEffect(() => {
    if (viewId !== undefined) window.localStorage.setItem(LAST_OPEN + instanceId, viewId)
  }, [instanceId, viewId])
  useEffect(() => {
    if (!screenOpen || viewId !== undefined || views === undefined) return
    const last = window.localStorage.getItem(LAST_OPEN + instanceId)
    const opening = views.find((view) => view.id === last) ?? views[0]
    landAt(opening === undefined ? homePath(instanceId) : viewPath(instanceId, opening.id))
  }, [instanceId, screenOpen, viewId, views])

  if (views === undefined) {
    return (
      <PageColumn className="py-6">
        <Skeleton className="h-72" />
      </PageColumn>
    )
  }
  const open = views.find((view) => view.id === viewId)
  const removed = () => {
    const at = views.findIndex((view) => view.id === viewId)
    const next = views[at + 1] ?? views[at - 1]
    landAt(next === undefined ? homePath(instanceId) : viewPath(instanceId, next.id))
    void listAgain()
  }
  const send = async (message: string, skills: string[]) => {
    if (viewId === undefined) return
    const created = await client.call("thread.create", {})
    await client.call("thread.turn.start", { thread_id: created.id, message, skills })
    selectThread(instanceId, created.id, { kind: "view", viewId })
  }
  return (
    <Tabs
      value={viewId ?? null}
      onValueChange={(picked: string) => openView(instanceId, picked)}
      className="my-6"
    >
      <div className="border-b">
        <PageColumn className="flex items-center gap-2">
          <TabsList
            variant="line"
            aria-label={`${name}'s views`}
            className="min-w-0 flex-1 justify-start overflow-x-auto"
          >
            {views.map((view) => (
              <TabsTrigger key={view.id} value={view.id} className="flex-none">
                {view.title}
              </TabsTrigger>
            ))}
          </TabsList>
          {open !== undefined && (
            <ViewMenu key={open.id} client={client} view={open} onDeleted={removed} />
          )}
        </PageColumn>
      </div>
      {viewId !== undefined && (
        <TabsContent value={viewId}>
          {/* The tab panel sets small text, and a view reads at the base size, as in the pane. */}
          <div className="pt-2 text-base">
            <ViewPage key={viewId} client={client} viewId={viewId} />
          </div>
          <div className="sticky bottom-0 bg-background pt-4">
            <PageColumn>
              <Composer
                draftKey={`screen-${instanceId}`}
                label="Message"
                placeholder={`Ask ${name} to change this view`}
                name={name}
                connected={connected}
                onSend={send}
                listSkills={listSkills}
              />
            </PageColumn>
          </div>
        </TabsContent>
      )}
    </Tabs>
  )
}

/** The open tab's menu: Delete, which asks first and says why the instance refused it. */
function ViewMenu({
  client,
  view,
  onDeleted,
}: {
  client: Pick<InstanceClient, "call">
  view: ViewSummary
  onDeleted: () => void
}) {
  const [asking, setAsking] = useState(false)
  const [refusal, setRefusal] = useState<unknown>()
  const remove = async () => {
    try {
      await client.call("view.delete", { id: view.id })
      setAsking(false)
      onDeleted()
    } catch (error) {
      setRefusal(error)
    }
  }
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="icon-sm" aria-label={`Menu for ${view.title}`} />}
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
    </>
  )
}
