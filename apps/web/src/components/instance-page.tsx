import type {
  Client,
  Clock,
  InstanceStatusResult,
  InstanceSummary,
  SecretSetup,
} from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { SubscriptionLogins } from "@/components/subscription-logins"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
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
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Item, ItemActions, ItemContent, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { followStart, type Starting } from "@/lib/creation"
import { instanceName } from "@/lib/instances"
import { BotIcon, CircleCheckIcon, CirclePauseIcon, CircleXIcon } from "lucide-react"

/**
 * What one instance shows. A stopped instance with setup pending opens on its setup card, and
 * keeps it until it runs. Any other stopped one offers Start. A running one offers its logins to
 * sign in again. `onChanged` hears a sign-in or a start end, so the instances are listed again,
 * and must keep its identity.
 */
export function InstancePage({
  caller,
  clock,
  instance,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onChanged: () => void
}) {
  // Read once: finishing the last sign-in on the card leaves the card open, with Start.
  const [openedOnSetup] = useState(instance.intended_state === "stopped" && instance.setup_pending)
  const { status, waiting } = useStatus(caller, instance)
  const name = instanceName(instance)
  const onSetup = openedOnSetup && instance.intended_state === "stopped"

  if (onSetup) {
    if (status === undefined) return null
    return (
      <SetupCard caller={caller} clock={clock} name={name} status={status} onChanged={onChanged} />
    )
  }
  if (instance.intended_state === "stopped") {
    return (
      <Stopped
        caller={caller}
        clock={clock}
        instanceId={instance.instance_id}
        name={name}
        onChanged={onChanged}
      />
    )
  }
  // The empty state waits until the status is read. A read that failed has nothing to sign in to.
  if (instance.intended_state === "running") {
    if (waiting && status === undefined) return null
    const logins = status?.setup.logins ?? []
    if (logins.length > 0) {
      return (
        <section className="flex max-w-2xl flex-col gap-3 p-6">
          <h2 className="font-medium">Subscription logins</h2>
          <p className="text-sm text-muted-foreground">
            Sign in again when a subscription stops working. The instance keeps running.
          </p>
          <SubscriptionLogins
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            logins={logins}
            onEnded={onChanged}
          />
        </section>
      )
    }
  }
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>Nothing here yet</EmptyTitle>
        <EmptyDescription>What {name} does will show here.</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

/**
 * The instance's status, read again each time the instance is listed again. The last one read
 * stays while the next is on its way, so the rows that follow a sign-in stay where they are.
 * `waiting` is true until the first read settles.
 */
function useStatus(
  caller: Pick<Client, "call">,
  instance: InstanceSummary,
): { status: InstanceStatusResult | undefined; waiting: boolean } {
  const [status, setStatus] = useState<InstanceStatusResult>()
  const [waiting, setWaiting] = useState(true)
  useEffect(() => {
    let current = true
    caller.call("instance.status", { instance_id: instance.instance_id }).then(
      (read) => {
        if (!current) return
        setStatus(read)
        setWaiting(false)
      },
      // A status that cannot be read keeps the one already shown.
      () => {
        if (current) setWaiting(false)
      },
    )
    return () => {
      current = false
    }
  }, [caller, instance])
  return { status, waiting }
}

function SetupCard({
  caller,
  clock,
  name,
  status,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  status: InstanceStatusResult
  onChanged: () => void
}) {
  const titleId = useId()
  const { logins, secrets } = status.setup
  return (
    <section aria-labelledby={titleId} className="p-6">
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle id={titleId}>Finish setting up {name}</CardTitle>
          <CardDescription>
            Sign in to what it declares, and set its secrets. It starts without them too.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-6">
            {logins.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Sign in</h3>
                <SubscriptionLogins
                  caller={caller}
                  clock={clock}
                  instanceId={status.instance_id}
                  logins={logins}
                  onEnded={onChanged}
                />
              </section>
            )}
            {secrets.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Secrets</h3>
                <Secrets secrets={secrets} />
              </section>
            )}
          </div>
        </CardContent>
        <CardFooter>
          <StartButton
            caller={caller}
            clock={clock}
            instanceId={status.instance_id}
            onStarted={onChanged}
          />
        </CardFooter>
      </Card>
    </section>
  )
}

function Stopped({
  caller,
  clock,
  instanceId,
  name,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  name: string
  onChanged: () => void
}) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-1">
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <CirclePauseIcon />
          </EmptyMedia>
          <EmptyTitle id={titleId}>{name} is stopped</EmptyTitle>
          <EmptyDescription>It does nothing until it starts.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <StartButton
            caller={caller}
            clock={clock}
            instanceId={instanceId}
            onStarted={onChanged}
          />
        </EmptyContent>
      </Empty>
    </section>
  )
}

function Secrets({ secrets }: { secrets: SecretSetup[] }) {
  return (
    <ItemGroup aria-label="Secrets">
      {secrets.map((secret) => (
        <Item
          key={secret.name}
          render={<li />}
          aria-label={secret.label}
          variant="outline"
          size="sm"
        >
          <ItemContent>
            <ItemTitle>{secret.label}</ItemTitle>
          </ItemContent>
          <ItemActions>
            {!secret.required && <Badge variant="secondary">Optional</Badge>}
            {secret.is_set ? (
              <Badge variant="success">
                <CircleCheckIcon data-icon="inline-start" />
                Set
              </Badge>
            ) : (
              <Badge variant="outline">Not set</Badge>
            )}
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

function StartButton({
  caller,
  clock,
  instanceId,
  onStarted,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  onStarted: () => void
}) {
  const [request, setRequest] = useState<{ instanceId: string }>()
  const start = useCallback(
    (asked: { instanceId: string }, report: (starting: Starting) => void) =>
      followStart(
        caller,
        asked.instanceId,
        (starting) => {
          report(starting)
          if (starting.state === "started") onStarted()
        },
        clock,
      ),
    [caller, clock, onStarted],
  )
  const starting = useFollowing(request, start)
  const busy = request !== undefined && starting?.state !== "failed"
  return (
    <div className="flex flex-col gap-3">
      {starting?.state === "failed" && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The start failed</AlertTitle>
          <AlertDescription>{starting.detail}</AlertDescription>
        </Alert>
      )}
      <div>
        <Button disabled={busy} onClick={() => setRequest({ instanceId })}>
          {busy && <Spinner data-icon="inline-start" />}
          Start
        </Button>
      </div>
    </div>
  )
}
