import type { Avatar as AvatarChoice, Client, Clock, InstanceClient } from "@kinby/contract"
import { useEffect, useRef, useSyncExternalStore } from "react"

import { ApprovalPanel } from "@/components/thread-approval"
import { ThreadComposer } from "@/components/thread-composer"
import { ThreadHeader } from "@/components/thread-header"
import { TakeoverPanel } from "@/components/thread-takeover"
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
  avatar,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  hub: Pick<Client, "call" | "subscribe">
  clock: Clock
  /** The hub's ID for the instance, which its pages' links name. */
  instanceId: string
  threadId: string
  /** What the instance is called. */
  name: string
  /** The instance's avatar, beside what it says. */
  avatar: AvatarChoice
}) {
  const speaker = { name, avatar }
  const store = threadStore(client, threadId)
  useEffect(() => store.follow(), [store])
  const { timeline, replayed, failure } = useSyncExternalStore(store.onChange, store.view)
  const notifications = useNotifications(hub, clock, threadId, timeline.turns)
  const latest = timeline.turns.at(-1)
  const approval = latest?.approval
  const takeover = latest?.takeover
  const reasonField = useRef<HTMLInputElement>(null)
  const approvalPanel = useRef<HTMLElement>(null)
  // A thread that opens on a parked approval has no composer to take focus, so its reason does.
  useEffect(() => {
    if (replayed) takeFocus(reasonField.current)
  }, [replayed])
  // An approval that parks later takes the composer's place, and focus falls to the page. The panel
  // itself catches it, not a field in it, so keys typed for the composer cannot deny it.
  useEffect(() => {
    if (document.activeElement === document.body) approvalPanel.current?.focus()
  }, [approval?.approvalId])

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
          />
          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
            {/* Nothing else is sent while the turn waits on the user. */}
            {approval !== undefined ? (
              <ApprovalPanel
                key={approval.approvalId}
                ref={approvalPanel}
                reasonField={reasonField}
                client={client}
                threadId={threadId}
                approval={approval}
                routine={latest?.startedBy.kind === "routine" ? latest.startedBy.name : undefined}
                speaker={speaker}
              />
            ) : latest !== undefined && takeover !== undefined ? (
              <TakeoverPanel
                key={takeover.takeoverId}
                client={client}
                hub={hub}
                instanceId={instanceId}
                threadId={threadId}
                turnId={latest.turnId}
                takeover={takeover}
                speaker={speaker}
              />
            ) : (
              <ThreadComposer
                client={client}
                threadId={threadId}
                name={name}
                running={latest !== undefined && latest.end === undefined}
              />
            )}
          </div>
        </>
      ) : (
        <TranscriptSkeleton />
      )}
    </div>
  )
}
