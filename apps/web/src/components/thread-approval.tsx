import type { ApprovalDecision, InstanceClient } from "@kinby/contract"
import { type Ref, useId, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import type { Speaker } from "@/components/thread-transcript"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import { mainArgument, type ParkedApproval } from "@/lib/timeline"
import {
  BookCheckIcon,
  CheckCheckIcon,
  CheckIcon,
  CircleStopIcon,
  ShieldAlertIcon,
  XIcon,
} from "lucide-react"

/** The gate rule of a call to an authored tool, which the user can mark read-only. */
const AUTHORED_RULE = "authored.write"

/** The button pressed in an approval panel. */
type Answer = ApprovalDecision | "stop"

/**
 * The approval a turn is parked on, in the composer's place: approve, deny with an optional reason
 * the model reads, or stop the turn. Enter in the reason denies with it. On a routine's turn it
 * also offers to approve the tool in every run of that routine, and on an authored tool's call to
 * mark the tool read-only.
 */
export function ApprovalPanel({
  ref,
  reasonField,
  client,
  threadId,
  approval,
  routine,
  speaker,
}: {
  ref: Ref<HTMLElement>
  reasonField: Ref<HTMLInputElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  approval: ParkedApproval
  /** The routine whose turn parked on the approval. */
  routine: string | undefined
  speaker: Speaker
}) {
  const reasonId = useId()
  const [typed, setTyped] = useState("")
  // An answer is final. The panel goes when the turn's next event arrives.
  const [answering, setAnswering] = useState<Answer>()
  const [failure, setFailure] = useState<string>()
  const call = [approval.name, mainArgument(approval.arguments)].filter(Boolean).join(" ")

  const answer = async (pressed: Answer, work: () => Promise<unknown>) => {
    setAnswering(pressed)
    try {
      await work()
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
      setAnswering(undefined)
    }
  }
  const respond = (decision: ApprovalDecision) => {
    const denial = typed.trim()
    return answer(decision, () =>
      client.call("thread.approval.respond", {
        thread_id: threadId,
        approval_id: approval.approvalId,
        decision,
        ...(decision === "deny" && denial !== "" && { reason: denial }),
      }),
    )
  }
  const stop = () =>
    answer("stop", () => client.call("thread.turn.interrupt", { thread_id: threadId }))
  const busy = answering !== undefined

  return (
    <section
      ref={ref}
      tabIndex={-1}
      aria-label={`Approve ${call}?`}
      className="flex gap-3 rounded-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {/* The instance asks in its own voice, beside the avatar it speaks with in the transcript. */}
      <InstanceAvatar avatar={speaker.avatar} name={speaker.name} />
      <Card className="min-w-0 flex-1">
        <CardContent>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-base font-semibold">Can I run {approval.name}?</h2>
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <ShieldAlertIcon aria-hidden className="size-3.5" />
                {approval.rule}
              </p>
            </div>
            <pre className="max-h-40 overflow-auto rounded-lg bg-muted p-4 font-mono text-xs whitespace-pre-wrap">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
            {approval.preview !== undefined && (
              <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-4 font-mono text-xs whitespace-pre-wrap">
                {approval.preview}
              </pre>
            )}
            <Field data-invalid={failure !== undefined || undefined}>
              <FieldLabel htmlFor={reasonId}>Reason to deny (optional)</FieldLabel>
              <Input
                ref={reasonField}
                id={reasonId}
                placeholder="Use the staging database instead"
                value={typed}
                disabled={busy}
                aria-invalid={failure !== undefined || undefined}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.nativeEvent.isComposing) return
                  event.preventDefault()
                  void respond("deny")
                }}
              />
              {failure !== undefined && <FieldError>{failure}</FieldError>}
            </Field>
            {/* Below md the buttons grow to a 44 px tap target, and Stop the turn wraps before it overflows. */}
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button
                size="lg"
                className="max-md:h-11"
                disabled={busy}
                onClick={() => void respond("approve")}
              >
                {answering === "approve" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <CheckIcon data-icon="inline-start" />
                )}
                Approve
              </Button>
              {routine !== undefined && (
                <Button
                  size="lg"
                  variant="secondary"
                  className="max-md:h-11"
                  disabled={busy}
                  onClick={() => void respond("allow_in_routine")}
                >
                  {answering === "allow_in_routine" ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <CheckCheckIcon data-icon="inline-start" />
                  )}
                  Always allow in this routine
                </Button>
              )}
              {approval.rule === AUTHORED_RULE && (
                <Button
                  size="lg"
                  variant="secondary"
                  className="max-md:h-11"
                  disabled={busy}
                  onClick={() => void respond("mark_read_only")}
                >
                  {answering === "mark_read_only" ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <BookCheckIcon data-icon="inline-start" />
                  )}
                  Mark read-only
                </Button>
              )}
              <Button
                size="lg"
                variant="outline"
                className="max-md:h-11"
                disabled={busy}
                onClick={() => void respond("deny")}
              >
                {answering === "deny" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <XIcon data-icon="inline-start" />
                )}
                Deny
              </Button>
              <Button
                variant="link"
                className="ml-auto max-md:h-11"
                disabled={busy}
                onClick={() => void stop()}
              >
                {answering === "stop" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <CircleStopIcon data-icon="inline-start" />
                )}
                Stop the turn
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </section>
  )
}
