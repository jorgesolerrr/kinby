import type { Client, Clock, InstanceClient, InstanceSummary } from "@kinby/contract"
import { useEffect, useRef, useSyncExternalStore } from "react"

import { ApprovalCard } from "@/components/thread-approval"
import { ThreadComposer } from "@/components/thread-composer"
import { ThreadHeader } from "@/components/thread-header"
import { ThreadSplit } from "@/components/thread-pane"
import { TakeoverCard, TakeoverPane } from "@/components/thread-takeover"
import { Transcript, TranscriptSkeleton } from "@/components/thread-transcript"
import { ViewPane } from "@/components/view-page"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { useNotifications } from "@/hooks/use-notifications"
import { useView } from "@/hooks/use-view"
import { when } from "@/lib/config-changes"
import { takeFocus } from "@/lib/focus"
import { type InstanceNotice, noticeKey, noticeTitle } from "@/lib/instances"
import { closePane, openCore, openPane, openSkills, openView, useOpenPane } from "@/lib/selection"
import { threadStore } from "@/lib/thread-store"
import { CircleAlertIcon, GlobeIcon, LayoutDashboardIcon, TriangleAlertIcon } from "lucide-react"

/** Where each notice is answered: an update in Core version, or the MCP server's pick in Skills. */
const ANSWERS: Record<
  InstanceNotice["code"],
  { label: string; open: (instanceId: string) => void }
> = {
  revision_behind: { label: "Update", open: openCore },
  template_behind: { label: "Update", open: openCore },
  hub_route_missing: { label: "Update", open: openCore },
  mcp_server_failed: { label: "Skills", open: openSkills },
}

/**
 * A thread's turns as a timeline, followed live once its history has loaded. The hub tells how
 * each notification the thread raised was delivered, and takes the user's answer to its options.
 * Each of the instance's `notices` shows above the composer. The pane the URL names opens beside
 * the thread while what it holds lasts: the browser, while the turn is parked on a takeover, or one
 * of the instance's views, followed live.
 */
export function ThreadPanel({
  client,
  hub,
  clock,
  instanceId,
  threadId,
  name,
  notices,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  hub: Pick<Client, "call" | "subscribe">
  clock: Clock
  /** The hub's ID for the instance, which its pages' links name. */
  instanceId: string
  threadId: string
  /** What the instance is called. */
  name: string
  notices: InstanceSummary["notices"]
}) {
  const store = threadStore(client, threadId)
  useEffect(() => store.follow(), [store])
  const { timeline, replayed, failure } = useSyncExternalStore(store.onChange, store.view)
  const notifications = useNotifications(hub, clock, threadId, timeline.turns)
  const latest = timeline.turns.at(-1)
  const approval = latest?.approval
  const takeover = latest?.takeover
  const approvalCard = useRef<HTMLElement>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  const approvalId = approval?.approvalId
  // The card takes focus from the composer, but not from the title being renamed, when the thread
  // opens on it or the turn parks on it. Once it is answered, focus goes back to the composer.
  useEffect(() => {
    if (!replayed) return
    if (approvalId === undefined) {
      takeFocus(composer.current)
      return
    }
    // The transcript stays hidden until it has scrolled to where the thread opens, and a hidden
    // card cannot take focus, so the card waits up to a second for it to show.
    let frame = 0
    const focusCard = (waited: number) => {
      const card = approvalCard.current
      if (card?.checkVisibility?.({ visibilityProperty: true }) === false && waited < 60) {
        frame = requestAnimationFrame(() => focusCard(waited + 1))
      } else if (document.activeElement === composer.current) card?.focus()
      else takeFocus(card)
    }
    focusCard(0)
    return () => cancelAnimationFrame(frame)
  }, [replayed, approvalId])
  const pane = useOpenPane()
  const takeoverOpen = pane?.kind === "takeover"
  const parkedOnTakeover = takeover !== undefined
  useEffect(() => {
    if (replayed && takeoverOpen && !parkedOnTakeover) closePane()
  }, [replayed, takeoverOpen, parkedOnTakeover])
  const viewId = pane?.kind === "view" ? pane.viewId : undefined
  const followed = useView(client, viewId)

  if (failure !== undefined) {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-6">
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>The thread did not load</AlertTitle>
          <AlertDescription>
            <p>{failure}</p>
            <p>Reload to try again.</p>
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </AlertAction>
        </Alert>
      </div>
    )
  }
  return (
    <ThreadSplit
      onClose={closePane}
      pane={
        viewId !== undefined
          ? {
              icon: LayoutDashboardIcon,
              title: followed.view?.title ?? "The view",
              status:
                followed.view === undefined ? "" : `Changed ${when(followed.view.updated_at)}`,
              openAsPage: () => openView(instanceId, viewId),
              body: <ViewPane key={viewId} followed={followed} />,
            }
          : replayed && takeoverOpen && latest !== undefined && takeover !== undefined
            ? {
                icon: GlobeIcon,
                title: takeover.site ?? "The browser",
                status: `${name} is waiting on you`,
                body: (
                  <TakeoverPane
                    key={takeover.takeoverId}
                    client={client}
                    hub={hub}
                    instanceId={instanceId}
                    threadId={threadId}
                    turnId={latest.turnId}
                    takeover={takeover}
                    name={name}
                  />
                ),
              }
            : undefined
      }
      thread={
        <div className="flex min-h-0 flex-1 flex-col">
          <ThreadHeader client={client} threadId={threadId} />
          {/* Half a history must not pass for all of it. */}
          {replayed ? (
            <>
              <Transcript
                turns={timeline.turns}
                notifications={notifications}
                client={client}
                threadId={threadId}
                instanceId={instanceId}
                approvalCard={
                  approval !== undefined && (
                    <ApprovalCard
                      key={approval.approvalId}
                      ref={approvalCard}
                      client={client}
                      threadId={threadId}
                      approval={approval}
                      routine={
                        latest?.startedBy.kind === "routine" ? latest.startedBy.name : undefined
                      }
                    />
                  )
                }
                takeoverCard={
                  latest !== undefined &&
                  takeover !== undefined && (
                    <TakeoverCard
                      key={takeover.takeoverId}
                      client={client}
                      threadId={threadId}
                      takeover={takeover}
                      name={name}
                      paneOpen={takeoverOpen}
                      onOpen={() => openPane({ kind: "takeover" })}
                    />
                  )
                }
              />
              <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-6 pb-6">
                {notices.map((notice) => (
                  <Notice key={noticeKey(notice)} notice={notice} instanceId={instanceId} />
                ))}
                <ThreadComposer
                  ref={composer}
                  client={client}
                  threadId={threadId}
                  name={name}
                  running={latest !== undefined && latest.end === undefined}
                />
              </div>
            </>
          ) : (
            <TranscriptSkeleton />
          )}
        </div>
      }
    />
  )
}

function Notice({ notice, instanceId }: { notice: InstanceNotice; instanceId: string }) {
  const { label, open } = ANSWERS[notice.code]
  return (
    <Alert>
      <TriangleAlertIcon />
      <AlertTitle>{noticeTitle(notice)}</AlertTitle>
      <AlertDescription className="wrap-anywhere">{notice.message}</AlertDescription>
      <AlertAction>
        <Button size="xs" variant="outline" onClick={() => open(instanceId)}>
          {label}
        </Button>
      </AlertAction>
    </Alert>
  )
}
