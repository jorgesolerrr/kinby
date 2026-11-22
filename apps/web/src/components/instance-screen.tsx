import type { Clock, InstanceClient, SkillSummary, ViewSummary } from "@kinby/contract"
import { ArrowLeftIcon, EllipsisIcon, LayoutDashboardIcon } from "lucide-react"
import { useCallback, useId, useMemo, useState, useSyncExternalStore } from "react"

import { Composer } from "@/components/composer"
import { Failure } from "@/components/config-alerts"
import { PageLink } from "@/components/factories-page"
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
import { Button, buttonVariants } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Item, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { ViewPage } from "@/components/view-page"
import { POLL_INTERVAL_MS, usePolled } from "@/hooks/use-polled"
import { when } from "@/lib/config-changes"
import { landAt, screenPath, selectThread, useViewOpen, viewPath } from "@/lib/selection"

type ScreenClient = Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">

/**
 * An instance's screen. With no view named in the URL, it holds a composer that starts a thread
 * with the instance and opens it, over a card per view, the most recently changed first, listed
 * again as `usePolled` says. A card opens its view on its own, with a way back to the screen, the
 * view's menu, and a composer that starts a thread with the view in the pane beside it.
 */
export function InstanceScreen({
  client,
  clock,
  instanceId,
  name,
}: {
  client: ScreenClient
  clock: Clock
  instanceId: string
  name: string
}) {
  const viewId = useViewOpen()
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const list = useCallback(async () => (await client.call("view.list", {})).views, [client])
  const listSkills = useCallback(async () => (await client.call("skill.list", {})).skills, [client])
  const [listed, listAgain] = usePolled(list, clock, connected, POLL_INTERVAL_MS)
  const views = useMemo(
    () => listed?.toSorted((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)),
    [listed],
  )

  if (viewId !== undefined) {
    const send = async (message: string, skills: string[]) => {
      const created = await client.call("thread.create", {})
      await client.call("thread.turn.start", { thread_id: created.id, message, skills })
      selectThread(instanceId, created.id, { kind: "view", viewId })
    }
    const removed = () => {
      landAt(screenPath(instanceId))
      void listAgain()
    }
    return (
      <OpenView
        client={client}
        instanceId={instanceId}
        name={name}
        viewId={viewId}
        view={views?.find((view) => view.id === viewId)}
        connected={connected}
        listSkills={listSkills}
        onSend={send}
        onDeleted={removed}
      />
    )
  }

  const send = async (message: string, skills: string[]) => {
    const created = await client.call("thread.create", {})
    await client.call("thread.turn.start", { thread_id: created.id, message, skills })
    selectThread(instanceId, created.id)
  }
  return (
    <PageColumn className="flex flex-col gap-8 py-6">
      <h1 className="font-heading text-2xl font-semibold">{name}</h1>
      <Composer
        draftKey={`screen-${instanceId}`}
        label="Message"
        placeholder={`Message ${name}`}
        name={name}
        connected={connected}
        onSend={send}
        listSkills={listSkills}
      />
      <ViewCards instanceId={instanceId} name={name} views={views} />
    </PageColumn>
  )
}

/** The instance's views, a card each, every card a link to its view. */
function ViewCards({
  instanceId,
  name,
  views,
}: {
  instanceId: string
  name: string
  views: ViewSummary[] | undefined
}) {
  const headingId = useId()
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="text-sm font-medium text-muted-foreground">
        Views
      </h2>
      {views === undefined ? (
        <Skeleton className="h-20" />
      ) : views.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No views yet. Ask {name} for one when a page would show something better than a message.
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {views.map((view) => (
            <li key={view.id}>
              <Item
                variant="outline"
                className="h-full"
                render={<PageLink href={viewPath(instanceId, view.id)} />}
              >
                <ItemMedia variant="icon">
                  <LayoutDashboardIcon />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>{view.title}</ItemTitle>
                  <ItemDescription>Changed {when(view.updated_at)}</ItemDescription>
                </ItemContent>
              </Item>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** One view on its own: a link back to the screen, its title and menu, the view, and a composer. */
function OpenView({
  client,
  instanceId,
  name,
  viewId,
  view,
  connected,
  listSkills,
  onSend,
  onDeleted,
}: {
  client: ScreenClient
  instanceId: string
  name: string
  viewId: string
  view: ViewSummary | undefined
  connected: boolean
  listSkills: () => Promise<SkillSummary[]>
  onSend: (message: string, skills: string[]) => Promise<unknown>
  onDeleted: () => void
}) {
  return (
    <div className="flex flex-col gap-2 py-6">
      <PageColumn className="flex items-center gap-2">
        <PageLink
          href={screenPath(instanceId)}
          aria-label={`Back to ${name}`}
          className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
        >
          <ArrowLeftIcon />
        </PageLink>
        <h1 className="min-w-0 flex-1 truncate font-heading text-xl font-semibold">
          {view?.title}
        </h1>
        {view !== undefined && (
          <ViewMenu key={view.id} client={client} view={view} onDeleted={onDeleted} />
        )}
      </PageColumn>
      <ViewPage key={viewId} client={client} viewId={viewId} />
      <div className="sticky bottom-0 bg-background pt-4">
        <PageColumn>
          <Composer
            draftKey={`view-${instanceId}`}
            label="Message"
            placeholder={`Ask ${name} to change this view`}
            name={name}
            connected={connected}
            onSend={onSend}
            listSkills={listSkills}
          />
        </PageColumn>
      </div>
    </div>
  )
}

/** The open view's menu: Delete, which asks first and says why the instance refused it. */
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
