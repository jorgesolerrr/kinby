import type { ApprovalDecision, InstanceClient } from "@kinby/contract"
import { type ReactNode, type Ref, useId, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import { mainArgument, type ParkedApproval } from "@/lib/timeline"
import { CheckIcon, ChevronDownIcon, ShieldAlertIcon, XIcon } from "lucide-react"

/** The gate rule of a call to an authored tool, which the user can mark read-only. */
const AUTHORED_RULE = "authored.write"

/** The answer pressed on an approval card. */
type Answer = ApprovalDecision | "stop"

/**
 * The approval a turn is parked on, as a card under the call that asked: approve, deny, or under
 * More deny with a reason the model reads, or stop the turn. Enter in the reason denies with it. On
 * a routine's turn More also offers to approve the tool in every run of that routine, and on an
 * authored tool's call to mark the tool read-only.
 */
export function ApprovalCard({
  ref,
  client,
  threadId,
  approval,
  routine,
}: {
  ref: Ref<HTMLElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  approval: ParkedApproval
  /** The routine whose turn parked on the approval. */
  routine: string | undefined
}) {
  const reasonId = useId()
  const reasonField = useRef<HTMLInputElement>(null)
  const [denying, setDenying] = useState(false)
  const [typed, setTyped] = useState("")
  // An answer is final. The card goes when the turn's next event arrives.
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
  const pending = (pressed: Answer, icon: ReactNode) =>
    answering === pressed ? <Spinner data-icon="inline-start" /> : icon

  return (
    // The section, not a field or button in it, takes focus, so keys typed elsewhere cannot answer.
    <section
      ref={ref}
      tabIndex={-1}
      aria-label={`Approve ${call}?`}
      className="rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <Card size="sm" variant="attention">
        <CardHeader>
          <CardTitle>
            <span className="flex items-center gap-2 wrap-anywhere">
              <ShieldAlertIcon aria-hidden className="size-4 shrink-0 text-ember" />
              Can I run {approval.name}?
            </span>
          </CardTitle>
          <CardDescription>
            <span className="font-mono text-xs wrap-anywhere">{approval.rule}</span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2">
            <pre className="max-h-40 overflow-y-auto rounded-lg bg-muted p-3 font-mono text-xs wrap-anywhere whitespace-pre-wrap">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
            {approval.preview !== undefined && (
              <pre className="max-h-80 overflow-y-auto rounded-lg bg-muted p-3 font-mono text-xs wrap-anywhere whitespace-pre-wrap">
                {approval.preview}
              </pre>
            )}
            {denying && (
              <Field data-invalid={failure !== undefined || undefined}>
                <FieldLabel htmlFor={reasonId}>Reason to deny</FieldLabel>
                <Input
                  ref={reasonField}
                  id={reasonId}
                  placeholder="Use the staging database instead"
                  value={typed}
                  disabled={busy}
                  onChange={(event) => setTyped(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" || event.nativeEvent.isComposing) return
                    event.preventDefault()
                    void respond("deny")
                  }}
                />
              </Field>
            )}
            {failure !== undefined && <FieldError>{failure}</FieldError>}
          </div>
        </CardContent>
        {/* Below md the buttons grow to a 44 px tap target, and More wraps before it overflows. */}
        <CardFooter>
          <div className="flex w-full flex-wrap items-center gap-2">
            <Button className="max-md:h-11" disabled={busy} onClick={() => void respond("approve")}>
              {pending("approve", <CheckIcon data-icon="inline-start" />)}
              Approve
            </Button>
            <Button
              variant="outline"
              className="max-md:h-11"
              disabled={busy}
              onClick={() => void respond("deny")}
            >
              {pending("deny", <XIcon data-icon="inline-start" />)}
              Deny
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                disabled={busy}
                render={<Button variant="ghost" className="ml-auto max-md:h-11" />}
              >
                {answering !== undefined && answering !== "approve" && answering !== "deny" && (
                  <Spinner data-icon="inline-start" />
                )}
                More
                <ChevronDownIcon data-icon="inline-end" />
              </DropdownMenuTrigger>
              {/* The reason just asked for takes focus from the menu, not its trigger. */}
              <DropdownMenuContent align="end" finalFocus={() => reasonField.current ?? true}>
                <DropdownMenuGroup>
                  {routine !== undefined && (
                    <DropdownMenuItem onClick={() => void respond("allow_in_routine")}>
                      Always allow in this routine
                    </DropdownMenuItem>
                  )}
                  {approval.rule === AUTHORED_RULE && (
                    <DropdownMenuItem onClick={() => void respond("mark_read_only")}>
                      Mark read-only
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => setDenying(true)}>
                    Deny with a reason…
                  </DropdownMenuItem>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuGroup>
                  <DropdownMenuItem variant="destructive" onClick={() => void stop()}>
                    Stop the turn
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardFooter>
      </Card>
    </section>
  )
}
