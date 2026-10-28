import type {
  Client,
  Clock,
  InstanceClient,
  InstanceStatusResult,
  InstanceSummary,
  LifecycleOperationResult,
  LoginSetup,
  RepositoryCommandList,
  ViewSummary,
} from "@kinby/contract"
import { type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { BrowserProfiles } from "@/components/browser-profiles"
import { InstanceAvatar } from "@/components/instance-avatar"
import { Secrets } from "@/components/secrets-section"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Vault } from "@/components/vault"
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
import { Item, ItemActions, ItemContent, ItemGroup, ItemTitle } from "@/components/ui/item"
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
import { openConfig, openHome, openMemory, openStats, openView } from "@/lib/selection"
import {
  BotIcon,
  BrainIcon,
  ChartColumnIcon,
  CirclePauseIcon,
  CircleStopIcon,
  CircleXIcon,
  EllipsisIcon,
  LayoutDashboardIcon,
  SlidersHorizontalIcon,
} from "lucide-react"

/**
 * What one instance shows. A stopped instance with setup pending opens on its setup card, and
 * keeps it until it runs. Any other stopped one offers Start. A running one opens its config panel,
 * its memory, its stats, and through `client`, its own connection, its views, offers Stop, and lists its logins, asking to sign in to those that are not signed in, or to sign in again once
 * all are. One the hub opens browsers for lists its browser profiles and its vault. Outside the setup card, an instance whose factory role needs repository commands shows
 * them.
 * Running or stopped, its ⋯ menu offers Remove, and a removal that succeeds goes home. The home
 * instance is never removed, so it has no ⋯ menu.
 * `onChanged` hears a sign-in, a start, a stop or a removal end, so the instances are listed again,
 * and must keep its identity. The hub marks the instance stopped before it drains, so a stop asked
 * here keeps the running view, and its Force stop, until the stop succeeds.
 */
export function InstancePage({
  caller,
  client,
  clock,
  instance,
  onChanged,
}: {
  caller: Pick<Client, "call">
  client?: Pick<InstanceClient, "call" | "subscribe">
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
  const removeMenu = !instance.home && (
    <RemoveMenu
      caller={caller}
      clock={clock}
      instanceId={instance.instance_id}
      name={name}
      onRemoved={removed}
    />
  )
  const intended = stopping ? "running" : instance.intended_state
  const onSetup = openedOnSetup && intended === "stopped"

  if (onSetup) {
    if (status === undefined) return null
    return (
      <SetupCard caller={caller} clock={clock} name={name} status={status} onChanged={onChanged}>
        {removeMenu}
      </SetupCard>
    )
  }
  const commands = status !== undefined && hasCommands(status) && (
    <RepositoryCommands name={name} status={status} />
  )
  if (intended === "stopped") {
    return (
      <>
        <Stopped
          caller={caller}
          clock={clock}
          instance={instance}
          name={name}
          onChanged={onChanged}
        >
          {removeMenu}
        </Stopped>
        {commands}
      </>
    )
  }
  // The empty state waits until the status is read. A read that failed has nothing to sign in to.
  if (intended === "running") {
    if (waiting && status === undefined) return null
    const logins = status?.setup.logins ?? []
    return (
      <>
        <header className="flex flex-col gap-4 px-6 pt-6">
          <h1 className="flex min-w-0 items-center gap-3 text-xl font-semibold">
            <InstanceAvatar avatar={instance.avatar} name={name} />
            <span className="truncate">{name}</span>
          </h1>
          {/* Its pages on the left, and what starts or ends the instance apart on the right. */}
          <div className="flex flex-wrap items-center gap-2">
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
            {client !== undefined && (
              <ViewsMenu client={client} instanceId={instance.instance_id} />
            )}
            <div className="ml-auto flex items-center gap-2">
              <StopButton
                caller={caller}
                clock={clock}
                instanceId={instance.instance_id}
                name={name}
                onStopping={() => setStopping(true)}
                onStopped={stopped}
              />
              {removeMenu}
            </div>
          </div>
        </header>
        <ProcessAlert instance={instance} name={name} />
        {logins.length > 0 && (
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
        )}
        {status?.browser === true && client !== undefined && (
          <>
            <BrowserProfiles
              caller={caller}
              client={client}
              clock={clock}
              instanceId={instance.instance_id}
            />
            <Vault caller={caller} clock={clock} instanceId={instance.instance_id} />
          </>
        )}
        {commands}
        {logins.length === 0 && !commands && observedState(instance) === "running" && (
          <ReadyForWork name={name} />
        )}
      </>
    )
  }
  return null
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
          <AlertDescription>
            <p>{followed.detail}</p>
            <p>Stop it again, or Force stop to end its work now.</p>
          </AlertDescription>
        </Alert>
      )}
    </>
  )
}

/**
 * Remove, once confirmed, from the ⋯ menu, and Force stop while the removal drains. A refusal stays
 * in the dialog with the hub's message. A force stop only escalates the drain: the removal's own
 * operation still says how it ends, even when it ends before the escalation lands.
 */
/** The instance's views, read each time the menu opens. Picking one opens its page. */
function ViewsMenu({
  client,
  instanceId,
}: {
  client: Pick<InstanceClient, "call">
  instanceId: string
}) {
  const [views, setViews] = useState<ViewSummary[]>()
  const [failure, setFailure] = useState<string>()
  const list = () => {
    setFailure(undefined)
    client.call("view.list", {}).then(
      (listed) => setViews(listed.views),
      (error: unknown) => setFailure(reason(error)),
    )
  }
  return (
    <DropdownMenu onOpenChange={(open) => open && list()}>
      <DropdownMenuTrigger render={<Button variant="outline" />}>
        <LayoutDashboardIcon data-icon="inline-start" />
        Views
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {failure !== undefined || views === undefined || views.length === 0 ? (
          <DropdownMenuItem disabled>
            {failure ?? (views === undefined ? "Reading…" : "No views yet")}
          </DropdownMenuItem>
        ) : (
          views.map((view) => (
            <DropdownMenuItem key={view.id} onClick={() => openView(instanceId, view.id)}>
              {view.title}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

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
  const [removal, setRemoval] = useState<LifecycleOperationResult>()
  const [forcing, setForcing] = useState(false)
  const follow = useCallback(
    (asked: LifecycleOperationResult, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => Promise.resolve(asked),
        (followed) => {
          report(followed)
          if (followed.state === "succeeded") onRemoved()
        },
        clock,
      ),
    [caller, clock, onRemoved],
  )
  const followed = useFollowing(removal, follow)
  const state = removal === undefined ? undefined : (followed?.state ?? "running")
  const remove = async () => {
    try {
      const asked = await caller.call("instance.remove", { instance_id: instanceId })
      setAsking(false)
      setForcing(false)
      setRemoval(asked)
    } catch (error) {
      setRefusal(reason(error))
    }
  }
  const force = () => {
    setForcing(true)
    // A refused escalation leaves the removal draining, so Force stop is offered again.
    void caller
      .call("instance.stop", { instance_id: instanceId, force: true })
      .catch(() => setForcing(false))
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
          <Button variant="destructive" disabled={forcing} onClick={force}>
            Force stop
          </Button>
        </>
      )}
      {followed?.state === "failed" && (
        <Alert variant="destructive" className="max-w-2xl basis-full">
          <CircleXIcon />
          <AlertTitle>The removal failed</AlertTitle>
          <AlertDescription>
            <p>{followed.detail}</p>
            <p>Remove it again from the ⋯ menu.</p>
          </AlertDescription>
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
          <p>
            {state === "restarting"
              ? "Its container keeps stopping, and the runtime keeps starting it again."
              : instance.detail}
          </p>
          <p>
            Check its settings in Configure before you stop it. A stopped instance has no Configure.
          </p>
        </AlertDescription>
      </Alert>
    </div>
  )
}

/** A running instance with nothing to set up, pointing to where its work happens. */
function ReadyForWork({ name }: { name: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>{name} is running</EmptyTitle>
        <EmptyDescription>
          Start a thread with New thread above {name}&apos;s threads, or open Configure to change
          what it does.
        </EmptyDescription>
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
  children,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  status: InstanceStatusResult
  onChanged: () => void
  /** Beside Start. */
  children: ReactNode
}) {
  const titleId = useId()
  const { logins, secrets, missing_commands: missing } = status.setup
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
            {missing.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Repository commands</h3>
                <p className="text-sm text-muted-foreground">
                  {name} takes no work until it holds these lists. Start {name} and ask it in a
                  thread for a repository survey.
                </p>
                <CommandLists held={status.repository_commands} missing={missing} />
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
            {children}
          </StartButton>
        </CardFooter>
      </Card>
    </section>
  )
}

function hasCommands(status: InstanceStatusResult): boolean {
  return status.repository_commands.length > 0 || status.setup.missing_commands.length > 0
}

/**
 * The repository commands the instance holds and those its factory role needs that it lacks.
 * They are read-only here: the instance records them.
 */
function RepositoryCommands({ name, status }: { name: string; status: InstanceStatusResult }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex max-w-2xl flex-col gap-3 p-6">
      <h2 id={titleId} className="font-medium">
        Repository commands
      </h2>
      <p className="text-sm text-muted-foreground">
        What {name} runs for the repository it works in. To change them, ask {name} in a thread.
      </p>
      <CommandLists held={status.repository_commands} missing={status.setup.missing_commands} />
    </section>
  )
}

/** Each list held, by name with its commands in the order they run, then each missing one. */
function CommandLists({ held, missing }: { held: RepositoryCommandList[]; missing: string[] }) {
  return (
    <ItemGroup aria-label="Repository commands">
      {held.map(({ name, commands }) => (
        <Item key={name} render={<li />} aria-label={name} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>
              <code className="font-mono">{name}</code>
            </ItemTitle>
            {commands.length > 0 && (
              <ol className="flex flex-col gap-1 text-muted-foreground">
                {commands.map((command, index) => (
                  // A list may run the same command twice.
                  <li key={index}>
                    <code className="font-mono break-all">{command}</code>
                  </li>
                ))}
              </ol>
            )}
          </ItemContent>
          {commands.length === 0 && (
            <ItemActions>
              <Badge variant="outline">Empty</Badge>
            </ItemActions>
          )}
        </Item>
      ))}
      {missing.map((list) => (
        <Item key={list} render={<li />} aria-label={list} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>
              <code className="font-mono">{list}</code>
            </ItemTitle>
          </ItemContent>
          <ItemActions>
            <Badge variant="outline">Missing</Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

/** Offers Start, and says how the last run ended when it crashed, since a stop keeps its error. */
function Stopped({
  caller,
  clock,
  instance,
  name,
  onChanged,
  children,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  name: string
  onChanged: () => void
  /** Beside Start. */
  children: ReactNode
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
            {children}
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
          <AlertDescription>
            <p>{starting.detail}</p>
            <p>Start it again to retry.</p>
          </AlertDescription>
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
