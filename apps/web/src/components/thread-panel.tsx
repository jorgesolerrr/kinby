import type { Client, Clock, InstanceClient } from "@kinby/contract"
import { useEffect, useRef, useSyncExternalStore } from "react"

import { ApprovalCard } from "@/components/thread-approval"
import { ThreadComposer } from "@/components/thread-composer"
import { ThreadHeader } from "@/components/thread-header"
import { TakeoverCard } from "@/components/thread-takeover"
import { Transcript, TranscriptSkeleton } from "@/components/thread-transcript"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { useNotifications } from "@/hooks/use-notifications"
import { takeFocus } from "@/lib/focus"
import { threadStore } from "@/lib/thread-store"
import { CircleAlertIcon } from "lucide-react"

/**
 * A thread's turns as a timeline, followed live once its history has loaded. The hub tells how
 * each notification the thread raised was delivered, and takes the user's answer to its options.
 */
export function ThreadPanel({
  client,
  hub,
  clock,
  instanceId,
  threadId,
  name,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  hub: Pick<Client, "call" | "subscribe">
  clock: Clock
  /** The hub's ID for the instance, which its pages' links name. */
  instanceId: string
  threadId: string
  /** What the instance is called. */
  name: string
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
    if (approvalId === undefined) takeFocus(composer.current)
    else if (document.activeElement === composer.current) approvalCard.current?.focus()
    else takeFocus(approvalCard.current)
  }, [replayed, approvalId])

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
                  routine={latest?.startedBy.kind === "routine" ? latest.startedBy.name : undefined}
                />
              )
            }
            takeoverCard={
              latest !== undefined &&
              takeover !== undefined && (
                <TakeoverCard
                  key={takeover.takeoverId}
                  client={client}
                  hub={hub}
                  instanceId={instanceId}
                  threadId={threadId}
                  turnId={latest.turnId}
                  takeover={takeover}
                  name={name}
                />
              )
            }
          />
          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
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
  )
}
