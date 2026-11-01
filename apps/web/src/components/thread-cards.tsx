import type { Notification } from "@kinby/contract"
import { useId, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldError } from "@/components/ui/field"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Spinner } from "@/components/ui/spinner"
import type { Notifications } from "@/hooks/use-notifications"
import { reason } from "@/lib/operation"
import { openPane, viewPath } from "@/lib/selection"
import type { NotificationStep, ViewStep } from "@/lib/timeline"
import { BellIcon, LayoutDashboardIcon } from "lucide-react"

/**
 * A notification the turn raised. One still waiting on the user's answer is a card with its
 * options; any other is a line with its title, where it stands, and how it was answered.
 */
export function RaisedNotification({
  step,
  notifications,
}: {
  step: NotificationStep
  notifications: Notifications
}) {
  const notificationId = step.hub?.kind === "accepted" ? step.hub.notificationId : undefined
  const kept = notificationId === undefined ? undefined : notifications.kept.get(notificationId)
  const status = notificationStatus(step, kept)
  const applied = kept?.replies.find((reply) => reply.option != null && reply.status === "applied")
  const answered =
    step.approval?.settled !== undefined
      ? SETTLED_APPROVALS[step.approval.settled]
      : applied && `Answered “${applied.option}”`
  if (step.options.length > 0 && step.hub?.kind !== "refused" && !answered) {
    return (
      <NotificationCard step={step} kept={kept} status={status} notifications={notifications} />
    )
  }
  return (
    <Marker variant={status?.variant === "destructive" ? "destructive" : "default"}>
      <MarkerIcon>
        <BellIcon />
      </MarkerIcon>
      <MarkerContent>
        {[step.title, status?.label, answered].filter(Boolean).join(" · ")}
      </MarkerContent>
    </Marker>
  )
}

/**
 * A notification waiting on the user's answer: what it said, where it stands, and its options. The
 * hub takes an answer once it holds the notification.
 */
function NotificationCard({
  step,
  kept,
  status,
  notifications,
}: {
  step: NotificationStep
  kept: Notification | undefined
  status: NotificationStatus | undefined
  notifications: Notifications
}) {
  const titleId = useId()
  const [answering, setAnswering] = useState<string>()
  const [failure, setFailure] = useState<string>()
  const latest = kept?.replies.filter((reply) => reply.option != null).at(-1)
  // An answer the hub could not apply leaves the options to answer again.
  const unapplied =
    latest?.status === "failed" || latest?.status === "stale" ? latest.outcome : undefined
  const shown = failure ?? unapplied ?? undefined

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
            {latest?.status === "received" ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <Spinner aria-hidden className="size-3.5" />
                {`Answering “${latest.option}”`}
              </p>
            ) : (
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
            )}
          </div>
        </CardContent>
      </Card>
    </article>
  )
}

/** How an approval's notification was answered, once the approval settled. */
const SETTLED_APPROVALS = { allow: "Approved", deny: "Denied", ended: "No longer waiting" }

interface NotificationStatus {
  label: string
  variant: "outline" | "secondary" | "destructive"
}

/**
 * Where a notification stands: waiting for the hub, refused by it, or how its channels took it.
 * One the hub took shows nothing until the hub tells.
 */
function notificationStatus(
  step: NotificationStep,
  kept: Notification | undefined,
): NotificationStatus | undefined {
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

/**
 * A view the turn created or changed, opening it in the pane beside the thread while it links to
 * the view's page, or one it deleted, struck through.
 */
export function ViewLine({ step, instanceId }: { step: ViewStep; instanceId: string }) {
  const icon = (
    <MarkerIcon>
      <LayoutDashboardIcon />
    </MarkerIcon>
  )
  if (step.change === "deleted") {
    return (
      <Marker>
        {icon}
        <MarkerContent>
          Deleted the view <del>{step.title}</del>
        </MarkerContent>
      </Marker>
    )
  }
  return (
    <Marker
      render={
        <a
          href={viewPath(instanceId, step.view)}
          onClick={(event) => {
            event.preventDefault()
            openPane({ kind: "view", viewId: step.view })
          }}
        >
          {icon}
          <MarkerContent>
            {step.change === "created" ? "Created" : "Changed"} the view{" "}
            <span className="text-foreground">{step.title}</span>
          </MarkerContent>
        </a>
      }
    />
  )
}
