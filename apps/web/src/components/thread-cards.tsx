import type { Notification } from "@kinby/contract"
import { useId, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldError } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import type { Notifications } from "@/hooks/use-notifications"
import { reason } from "@/lib/operation"
import { openView, viewPath } from "@/lib/selection"
import type { NotificationStep, ViewStep } from "@/lib/timeline"
import {
  BanIcon,
  BellIcon,
  CheckIcon,
  CircleStopIcon,
  LayoutDashboardIcon,
  type LucideIcon,
} from "lucide-react"

/**
 * A notification the turn raised: what it said, where it stands, and its options. The hub takes an
 * answer once it holds the notification, and the user's answer takes the options' place.
 */
export function NotificationCard({
  step,
  notifications,
}: {
  step: NotificationStep
  notifications: Notifications
}) {
  const titleId = useId()
  const [answering, setAnswering] = useState<string>()
  const [failure, setFailure] = useState<string>()
  const notificationId = step.hub?.kind === "accepted" ? step.hub.notificationId : undefined
  const kept = notificationId === undefined ? undefined : notifications.kept.get(notificationId)
  const replies = kept?.replies.filter((reply) => reply.option != null) ?? []
  const applied = replies.find((reply) => reply.status === "applied")
  const latest = replies.at(-1)
  // An answer the hub could not apply leaves the options to answer again.
  const unapplied =
    latest?.status === "failed" || latest?.status === "stale" ? latest.outcome : undefined
  const shown = failure ?? unapplied ?? undefined
  const status = notificationStatus(step, kept)
  const settled =
    step.approval?.settled === undefined ? undefined : SETTLED_APPROVALS[step.approval.settled]

  const answer = async (id: string, option: string) => {
    setAnswering(option)
    try {
      await notifications.answer(id, option)
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setAnswering(undefined)
    }
  }

  return (
    <article aria-labelledby={titleId}>
      <Card size="sm">
        <CardHeader>
          <CardTitle id={titleId}>
            <span className="flex items-center gap-2">
              <BellIcon aria-hidden className="size-4 shrink-0" />
              {step.title}
            </span>
          </CardTitle>
          {status !== undefined && (
            <CardAction>
              <Badge variant={status.variant}>{status.label}</Badge>
            </CardAction>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3">
            <p className="whitespace-pre-wrap">{step.text}</p>
            {settled !== undefined ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <settled.icon aria-hidden className="size-3.5" />
                {settled.label}
              </p>
            ) : applied !== undefined ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <CheckIcon aria-hidden className="size-3.5" />
                {`Answered “${applied.option}”`}
              </p>
            ) : latest?.status === "received" ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <Spinner aria-hidden className="size-3.5" />
                {`Answering “${latest.option}”`}
              </p>
            ) : (
              step.options.length > 0 && (
                <Field data-invalid={shown !== undefined || undefined}>
                  <div className="flex flex-wrap gap-2">
                    {step.options.map((option) => (
                      <Button
                        key={option}
                        size="sm"
                        variant="outline"
                        disabled={kept === undefined || answering !== undefined}
                        onClick={() => {
                          if (kept !== undefined) void answer(kept.notification_id, option)
                        }}
                      >
                        {answering === option && <Spinner data-icon="inline-start" />}
                        {option}
                      </Button>
                    ))}
                  </div>
                  {shown !== undefined && <FieldError>{shown}</FieldError>}
                </Field>
              )
            )}
          </div>
        </CardContent>
      </Card>
    </article>
  )
}

/** What an approval's notification shows in place of its options once the approval settled. */
const SETTLED_APPROVALS = {
  allow: { icon: CheckIcon, label: "Approved" },
  deny: { icon: BanIcon, label: "Denied" },
  ended: { icon: CircleStopIcon, label: "No longer waiting" },
} satisfies Record<string, { icon: LucideIcon; label: string }>

/**
 * Where a notification stands: waiting for the hub, refused by it, or how its channels took it.
 * One the hub took shows nothing until the hub tells.
 */
function notificationStatus(
  step: NotificationStep,
  kept: Notification | undefined,
): { label: string; variant: "outline" | "secondary" | "destructive" } | undefined {
  if (step.hub === undefined) return { label: "Waiting for the hub", variant: "secondary" }
  if (step.hub.kind === "refused") {
    return { label: `Not sent: ${step.hub.reason}`, variant: "destructive" }
  }
  if (kept === undefined) return undefined
  const statuses = new Set(kept.deliveries.map((delivery) => delivery.status))
  if (statuses.has("sent")) return { label: "Delivered", variant: "outline" }
  if (statuses.has("pending")) return { label: "Sending", variant: "secondary" }
  if (statuses.has("failed")) return { label: "Not delivered", variant: "destructive" }
  if (statuses.has("muted")) return { label: "Muted", variant: "outline" }
  return { label: "No channel", variant: "outline" }
}

/** A view the turn created or changed, linking to its page, or one it deleted, struck through. */
export function ViewChangedCard({ step, instanceId }: { step: ViewStep; instanceId: string }) {
  const titleId = useId()
  if (step.change === "deleted") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LayoutDashboardIcon aria-hidden className="size-4 shrink-0" />
        <del>{step.title}</del>
      </p>
    )
  }
  return (
    <article aria-labelledby={titleId}>
      <Card size="sm">
        <CardHeader>
          <CardTitle id={titleId}>
            <a
              className="flex items-center gap-2"
              href={viewPath(instanceId, step.view)}
              onClick={(event) => {
                event.preventDefault()
                openView(instanceId, step.view)
              }}
            >
              <LayoutDashboardIcon aria-hidden className="size-4 shrink-0" />
              {step.title}
            </a>
          </CardTitle>
          <CardAction>
            <Badge variant="outline">{step.change === "created" ? "Created" : "Changed"}</Badge>
          </CardAction>
        </CardHeader>
      </Card>
    </article>
  )
}
