import type {
  Client,
  Clock,
  InstanceStatusResult,
  InstanceSummary,
  LifecycleOperationResult,
  LoginSetup,
} from "@kinby/contract"
import { type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { Secrets } from "@/components/secrets-section"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { followStart, type Starting } from "@/lib/creation"
import { instanceName, observedState } from "@/lib/instances"
import { type Followed, followOperation, reason } from "@/lib/operation"
import { openConfig, openHome, openMemory, openStats } from "@/lib/selection"
import {
  BotIcon,
  BrainIcon,
  ChartColumnIcon,
  CirclePauseIcon,
  CircleStopIcon,
  CircleXIcon,
  EllipsisIcon,
  SlidersHorizontalIcon,
} from "lucide-react"

/**
 * What one instance shows. A stopped instance with setup pending opens on its setup card, and
 * keeps it until it runs. Any other stopped one offers Start. A running one opens its config panel,
 * its memory, and its stats, offers Stop, and lists its logins, asking to sign in to those that are not signed in, or to sign in again once
 * all are.
 * Running or stopped, its ⋯ menu offers Remove, and a removal that succeeds goes home.
 * `onChanged` hears a sign-in, a start, a stop or a removal end, so the instances are listed again,
 * and must keep its identity. The hub marks the instance stopped before it drains, so a stop asked
 * here keeps the running view, and its Force stop, until the stop succeeds.
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
  const [stopping, setStopping] = useState(false)
  const stopped = useCallback(() => {
    setStopping(false)
    onChanged()
  }, [onChanged])
  const removed = useCallback(() => {
    onChanged()
    openHome()
  }, [onChanged])
  const { status, waiting } = useStatus(caller, instance)
  const name = instanceName(instance)
  const intended = stopping ? "running" : instance.intended_state
  const onSetup = openedOnSetup && intended === "stopped"

  if (onSetup) {
    if (status === undefined) return null
    return (
      <SetupCard
        caller={caller}
        clock={clock}
        name={name}
        status={status}
        onChanged={onChanged}
        onRemoved={removed}
      />
    )
  }
  if (intended === "stopped") {
    return (
      <Stopped
        caller={caller}
        clock={clock}
        instance={instance}
        name={name}
        onChanged={onChanged}
        onRemoved={removed}
      />
    )
  }
  // The empty state waits until the status is read. A read that failed has nothing to sign in to.
  if (intended === "running") {
    if (waiting && status === undefined) return null
    const logins = status?.setup.logins ?? []
    return (
      <>
        <div className="flex flex-wrap items-center gap-2 px-6 pt-6">
          <Button variant="outline" onClick={() => openConfig(instance.instance_id)}>
            <SlidersHorizontalIcon data-icon="inline-start" />
            Configure
          </Button>
          <Button variant="outline" onClick={() => openMemory(instance.instance_id)}>
            <BrainIcon data-icon="inline-start" />
            Memory
          </Button>
          <Button variant="outline" onClick={() => openStats(instance.instance_id)}>
            <ChartColumnIcon data-icon="inline-start" />
            Stats
          </Button>
          <StopButton
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            name={name}
            onStopping={() => setStopping(true)}
            onStopped={stopped}
          />
          <RemoveMenu
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            name={name}
            onRemoved={removed}
          />
        </div>
        <ProcessAlert instance={instance} name={name} />
        {logins.length > 0 ? (
          <section className="flex max-w-2xl flex-col gap-3 p-6">
            <h2 className="font-medium">Subscription logins</h2>
            <p className="text-sm text-muted-foreground">
              {loginsNote(logins)} The instance keeps running.
            </p>
            <SubscriptionLogins
              caller={caller}
              clock={clock}
              instanceId={instance.instance_id}
              logins={logins}
              onEnded={onChanged}
            />
          </section>
        ) : (
          <NothingHereYet name={name} />
        )}
      </>
    )
  }
  return <NothingHereYet name={name} />
}

/**
 * Stop, once confirmed, and Force stop while a stop runs or after one failed. A force stop asked
 * while a stop drains escalates it, and the hub answers with that stop's operation.
 */
function StopButton({
  caller,
  clock,
  instanceId,
  name,
  onStopping,
  onStopped,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  name: string
  onStopping: () => void
  onStopped: () => void
}) {
  const [asking, setAsking] = useState(false)
  // A new object for each click, so stopping again follows the operation the hub answers with.
  const [request, setRequest] = useState<{ force: boolean }>()
  const stop = useCallback(
    ({ force }: { force: boolean }, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () =>
          caller.call(
            "instance.stop",
            force ? { instance_id: instanceId, force } : { instance_id: instanceId },
          ),
        (followed) => {
          report(followed)
          if (followed.state === "succeeded") onStopped()
        },
        clock,
      ),
    [caller, clock, instanceId, onStopped],
  )
  const followed = useFollowing(request, stop)
  const state = request === undefined ? undefined : (followed?.state ?? "running")
  const busy = state === "running" || state === "succeeded"
  return (
    <>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogTrigger render={<Button variant="outline" disabled={busy} />}>
          {busy ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <CircleStopIcon data-icon="inline-start" />
          )}
          Stop
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {name} finishes the work it has accepted, then does nothing until it starts again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setAsking(false)
                onStopping()
                setRequest({ force: false })
              }}
            >
              Stop
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {(state === "running" || state === "failed") && (
        <Button
          variant="destructive"
          disabled={state === "running" && request?.force}
          onClick={() => setRequest({ force: true })}
        >
          Force stop
        </Button>
      )}
      {followed?.state === "failed" && (
        <Alert variant="destructive" className="max-w-2xl basis-full">
          <CircleXIcon />
          <AlertTitle>The stop failed</AlertTitle>
          <AlertDescription>{followed.detail}</AlertDescription>
        </Alert>
      )}
    </>
  )
}

/** A removal asked for, or a force stop of the one that drains. */
interface Removing {
  begin: () => Promise<LifecycleOperationResult>
  force: boolean
}

/**
 * Remove, once confirmed, from the ⋯ menu, and Force stop while the removal drains. A refusal stays
 * in the dialog with the hub's message. A force stop escalates the pending removal, and the hub
 * answers with the removal's operation.
 */
function RemoveMenu({
  caller,
  clock,
  instanceId,
  name,
  onRemoved,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  name: string
  onRemoved: () => void
}) {
  const [asking, setAsking] = useState(false)
  const [refusal, setRefusal] = useState<string>()
  // A new object for each request, so a force stop follows the operation the hub answers with.
  const [request, setRequest] = useState<Removing>()
  const follow = useCallback(
    (asked: Removing, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        asked.begin,
        (followed) => {
          report(followed)
          if (followed.state === "succeeded") onRemoved()
        },
        clock,
      ),
    [caller, clock, onRemoved],
  )
  const followed = useFollowing(request, follow)
  const state = request === undefined ? undefined : (followed?.state ?? "running")
  const remove = async () => {
    try {
      const removal = await caller.call("instance.remove", { instance_id: instanceId })
      setAsking(false)
      setRequest({ begin: () => Promise.resolve(removal), force: false })
    } catch (error) {
      setRefusal(reason(error))
    }
  }
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="outline"
              size="icon"
              aria-label="More actions"
              disabled={state === "running" || state === "succeeded"}
            />
          }
        >
          <EllipsisIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onClick={() => {
              setRefusal(undefined)
              setAsking(true)
            }}
          >
            Remove
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {name} stops and leaves the sidebar. Its data stays, and you can restore it from
              Removed instances.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {refusal !== undefined && (
            <Alert variant="destructive">
              <CircleXIcon />
              <AlertTitle>The hub did not remove {name}</AlertTitle>
              <AlertDescription>{refusal}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void remove()}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {state === "running" && (
        <>
          <Badge variant="secondary">
            <Spinner data-icon="inline-start" />
            Removing…
          </Badge>
          <Button
            variant="destructive"
            disabled={request?.force}
            onClick={() =>
              setRequest({
                begin: () => caller.call("instance.stop", { instance_id: instanceId, force: true }),
                force: true,
              })
            }
          >
            Force stop
          </Button>
        </>
      )}
      {followed?.state === "failed" && (
        <Alert variant="destructive" className="max-w-2xl basis-full">
          <CircleXIcon />
          <AlertTitle>The removal failed</AlertTitle>
          <AlertDescription>{followed.detail}</AlertDescription>
        </Alert>
      )}
    </>
  )
}

/** Says so when an instance meant to run is restarting in a loop or has failed. */
function ProcessAlert({ instance, name }: { instance: InstanceSummary; name: string }) {
  const state = observedState(instance)
  if (state !== "restarting" && state !== "failed") return null
  return (
    <div className="max-w-2xl px-6 pt-6">
      <Alert variant="destructive">
        <CircleXIcon />
        <AlertTitle>
          {state === "restarting" ? `${name} is restarting` : `${name} failed`}
        </AlertTitle>
        <AlertDescription>
          {state === "restarting"
            ? "Its container keeps stopping, and the runtime keeps starting it again."
            : instance.detail}
        </AlertDescription>
      </Alert>
    </div>
  )
}

function NothingHereYet({ name }: { name: string }) {
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

/** Names the logins not signed in, pending or failed, or offers signing in again once none is. */
function loginsNote(logins: LoginSetup[]): string {
  const notSignedIn = logins.filter((login) => login.state !== "signed_in")
  if (notSignedIn.length === 0) return "Sign in again when a subscription stops working."
  const names = new Intl.ListFormat("en").format(notSignedIn.map((login) => login.label))
  return notSignedIn.length === 1
    ? `${names} is not signed in yet. Sign in to let the instance use it.`
    : `${names} are not signed in yet. Sign in to let the instance use them.`
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
  onRemoved,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  status: InstanceStatusResult
  onChanged: () => void
  onRemoved: () => void
}) {
  const titleId = useId()
  const { logins, secrets } = status.setup
  return (
    <section aria-labelledby={titleId} className="p-6">
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle id={titleId}>Finish setting up {name}</CardTitle>
          <CardDescription>
            Sign in to what it declares, and set its secrets. It starts without them too. A secret
            you set applies when {name} starts.
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
                <Secrets
                  caller={caller}
                  clock={clock}
                  instanceId={status.instance_id}
                  secrets={secrets}
                  onSet={onChanged}
                />
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
          >
            <RemoveMenu
              caller={caller}
              clock={clock}
              instanceId={status.instance_id}
              name={name}
              onRemoved={onRemoved}
            />
          </StartButton>
        </CardFooter>
      </Card>
    </section>
  )
}

/** Offers Start, and says how the last run ended when it crashed, since a stop keeps its error. */
function Stopped({
  caller,
  clock,
  instance,
  name,
  onChanged,
  onRemoved,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  name: string
  onChanged: () => void
  onRemoved: () => void
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
          {instance.process === "failed" && (
            <EmptyDescription>The last run ended with an error: {instance.detail}</EmptyDescription>
          )}
        </EmptyHeader>
        <EmptyContent>
          <StartButton
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            onStarted={onChanged}
          >
            <RemoveMenu
              caller={caller}
              clock={clock}
              instanceId={instance.instance_id}
              name={name}
              onRemoved={onRemoved}
            />
          </StartButton>
        </EmptyContent>
      </Empty>
    </section>
  )
}

/** Start, with `children` beside it. */
function StartButton({
  caller,
  clock,
  instanceId,
  onStarted,
  children,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  onStarted: () => void
  children: ReactNode
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
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy} onClick={() => setRequest({ instanceId })}>
          {busy && <Spinner data-icon="inline-start" />}
          Start
        </Button>
        {children}
      </div>
    </div>
  )
}
