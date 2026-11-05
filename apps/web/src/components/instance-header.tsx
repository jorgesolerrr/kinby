import type { Client, Clock, InstanceSummary, LifecycleOperationResult } from "@kinby/contract"
import {
  CircleStopIcon,
  CircleXIcon,
  DownloadIcon,
  EllipsisIcon,
  PlayIcon,
  RefreshCwIcon,
  TrashIcon,
} from "lucide-react"
import { type ReactNode, useCallback, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { OperationSteps } from "@/components/operation-steps"
import { PageColumn } from "@/components/page-column"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { useStart } from "@/hooks/use-start"
import { type Update, useUpdate } from "@/hooks/use-update"
import {
  type InstanceNotice,
  instanceName,
  noticeTitle,
  observedState,
  shownState,
} from "@/lib/instances"
import { type Followed, followOperation, reason } from "@/lib/operation"
import { openConfig, openCore, openHome } from "@/lib/selection"

/** What the header lends the page under it. */
export interface Lifecycle {
  /** Start the instance, as the header's Start does. */
  start: () => void
  /** A start asked here runs. */
  starting: boolean
  /** A stop asked here drains. The hub marks the instance stopped before it does. */
  stopping: boolean
  /** The instance's one update, which the header's update alert runs too. */
  update: Update
}

/** The notices an instance update answers. */
const UPDATE_ANSWERS: InstanceNotice["code"][] = [
  "revision_behind",
  "template_behind",
  "hub_route_missing",
]

const STATES: Record<ReturnType<typeof shownState>, string> = {
  running: "Running",
  starting: "Starting",
  restarting: "Restarting",
  stopped: "Stopped",
  failed: "Failed",
  created: "Created",
  missing: "Missing",
  unavailable: "Unavailable",
}

/**
 * The instance above its tabs: its avatar, its name, what the runtime saw it doing, and its
 * lifecycle. One meant to be stopped offers Start, and one meant to run Stop, once confirmed, and
 * Force stop while the stop drains or after it failed. The hub marks the instance stopped before
 * it drains, so a stop asked here keeps Stop, and its Force stop, until the stop succeeds. The ⋯
 * menu opens Core version, and offers Remove on any but the home instance; a removal that
 * succeeds goes home. Under it, alerts say when a start, a stop or a removal failed, when an
 * instance meant to run is restarting in a loop or failed, and when an instance update answers a
 * notice, with Update.
 * `onChanged` hears a start, a stop, an update or a removal end, so the instances are listed
 * again, and must keep its identity. `children` draws the page under the header, lent its lifecycle.
 */
export function InstanceHeader({
  caller,
  clock,
  instance,
  onChanged,
  children,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onChanged: () => void
  children?: (lifecycle: Lifecycle) => ReactNode
}) {
  const name = instanceName(instance)
  const start = useStart(caller, clock, instance, onChanged)
  const stop = useStop(caller, clock, instance, onChanged)
  const removal = useRemoval(caller, clock, instance, onChanged)
  const update = useUpdate(caller, clock, instance.instance_id, onChanged)
  const intended = stop.stopping ? "running" : instance.intended_state
  const state = STATES[shownState(instance)]
  const revision = instance.source_revision.slice(0, 7)
  const status =
    removal.state === "running"
      ? "Removing…"
      : revision === ""
        ? state
        : `${state} · core ${revision}`
  return (
    <>
      <header className="shrink-0">
        <PageColumn className="flex flex-col gap-4 pt-6">
          <div className="flex flex-wrap items-center gap-3">
            <InstanceAvatar avatar={instance.avatar} name={name} size="lg" />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex min-w-0 items-center gap-2">
                <h1 className="truncate font-heading text-xl font-semibold">{name}</h1>
                {instance.home && <Badge variant="outline">Home</Badge>}
              </div>
              <p className="truncate text-sm text-muted-foreground">{status}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {intended === "stopped" && (
                <Button disabled={start.busy} onClick={start.ask}>
                  {start.busy ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <PlayIcon data-icon="inline-start" />
                  )}
                  Start
                </Button>
              )}
              {intended === "running" && <StopButton name={name} stop={stop} />}
              {removal.state === "running" && (
                <Button variant="destructive" disabled={removal.forcing} onClick={removal.force}>
                  Force stop
                </Button>
              )}
              <MoreMenu instance={instance} name={name} removal={removal} />
            </div>
          </div>
          {intended === "running" && <ProcessAlert instance={instance} name={name} />}
          <UpdateAlert instance={instance} update={update} />
          {start.starting?.state === "failed" && (
            <Failure title="The start failed" detail={start.starting.detail}>
              Start it again to retry.
            </Failure>
          )}
          {stop.followed?.state === "failed" && (
            <Failure title="The stop failed" detail={stop.followed.detail}>
              Stop it again, or Force stop to end its work now.
            </Failure>
          )}
          {removal.followed?.state === "failed" && (
            <Failure title="The removal failed" detail={removal.followed.detail}>
              Remove it again from the ⋯ menu.
            </Failure>
          )}
        </PageColumn>
      </header>
      {children?.({ start: start.ask, starting: start.busy, stopping: stop.stopping, update })}
    </>
  )
}

type Stop = ReturnType<typeof useStop>

/**
 * The stop asked here, and how it goes. A force stop asked while a stop drains escalates it, and
 * the hub answers with that stop's operation.
 */
function useStop(
  caller: Pick<Client, "call">,
  clock: Clock,
  instance: InstanceSummary,
  onStopped: () => void,
) {
  const instanceId = instance.instance_id
  const [stopping, setStopping] = useState(false)
  // A new object for each click, so stopping again follows the operation the hub answers with.
  const [request, setRequest] = useState<{ force: boolean }>()
  const follow = useCallback(
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
          if (followed.state !== "succeeded") return
          setStopping(false)
          onStopped()
        },
        clock,
      ),
    [caller, clock, instanceId, onStopped],
  )
  const followed = useFollowing(request, follow)
  // As a start does, a stop ends once the instances are listed again with the instance stopped.
  if (followed?.state === "succeeded" && instance.intended_state === "stopped") {
    setRequest(undefined)
  }
  const state = request === undefined ? undefined : (followed?.state ?? "running")
  return {
    stopping,
    followed,
    state,
    forced: request?.force === true,
    ask: (force: boolean) => {
      setStopping(true)
      setRequest({ force })
    },
  }
}

/** Stop, once confirmed, and Force stop while the stop runs or after it failed. */
function StopButton({ name, stop }: { name: string; stop: Stop }) {
  const [asking, setAsking] = useState(false)
  const busy = stop.state === "running" || stop.state === "succeeded"
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
                stop.ask(false)
              }}
            >
              Stop
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {(stop.state === "running" || stop.state === "failed") && (
        <Button
          variant="destructive"
          disabled={stop.state === "running" && stop.forced}
          onClick={() => stop.ask(true)}
        >
          Force stop
        </Button>
      )}
    </>
  )
}

type Removal = ReturnType<typeof useRemoval>

/**
 * The removal asked here, and how it goes. A refusal stays in the dialog with the hub's message.
 * A force stop only escalates the drain: the removal's own operation still says how it ends, even
 * when it ends before the escalation lands.
 */
function useRemoval(
  caller: Pick<Client, "call">,
  clock: Clock,
  instance: InstanceSummary,
  onChanged: () => void,
) {
  const instanceId = instance.instance_id
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
          if (followed.state !== "succeeded") return
          onChanged()
          openHome()
        },
        clock,
      ),
    [caller, clock, onChanged],
  )
  const followed = useFollowing(removal, follow)
  return {
    asking,
    refusal,
    followed,
    forcing,
    state: removal === undefined ? undefined : (followed?.state ?? "running"),
    ask: () => {
      setRefusal(undefined)
      setAsking(true)
    },
    setAsking,
    remove: async () => {
      try {
        const asked = await caller.call("instance.remove", { instance_id: instanceId })
        setAsking(false)
        setForcing(false)
        setRemoval(asked)
      } catch (error) {
        setRefusal(reason(error))
      }
    },
    force: () => {
      setForcing(true)
      // A refused escalation leaves the removal draining, so Force stop is offered again.
      void caller
        .call("instance.stop", { instance_id: instanceId, force: true })
        .catch(() => setForcing(false))
    },
  }
}

/** The ⋯ menu: Update core, and Remove, once confirmed, on any but the home instance. */
function MoreMenu({
  instance,
  name,
  removal,
}: {
  instance: InstanceSummary
  name: string
  removal: Removal
}) {
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              aria-label="More actions"
              disabled={removal.state === "running" || removal.state === "succeeded"}
            />
          }
        >
          <EllipsisIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => openCore(instance.instance_id)}>
            <RefreshCwIcon />
            Update core
          </DropdownMenuItem>
          {!instance.home && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={removal.ask}>
                <TrashIcon />
                Remove
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={removal.asking} onOpenChange={removal.setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {name} stops and leaves the sidebar. Its data stays, and you can restore it from
              Removed instances.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {removal.refusal !== undefined && (
            <Alert variant="destructive">
              <CircleXIcon />
              <AlertTitle>The hub did not remove {name}</AlertTitle>
              <AlertDescription>{removal.refusal}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void removal.remove()}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/** Says so when an instance meant to run is restarting in a loop or has failed. */
function ProcessAlert({ instance, name }: { instance: InstanceSummary; name: string }) {
  const state = observedState(instance)
  if (state !== "restarting" && state !== "failed") return null
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{state === "restarting" ? `${name} is restarting` : `${name} failed`}</AlertTitle>
      <AlertDescription>
        <p>
          {state === "restarting"
            ? "Its container keeps stopping, and the runtime keeps starting it again."
            : instance.detail}
        </p>
        <p>
          Check its settings in Configure before you stop it. A stopped instance has no Configure.
        </p>
        <div className="pt-1">
          <Button size="sm" variant="outline" onClick={() => openConfig(instance.instance_id)}>
            Open Config
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  )
}

/**
 * Names the notice an instance update answers, with Update to the hub's revision, and how the
 * update goes. The hub records the new revision before the replacement starts, so a relist can
 * drop the notice mid-update: the alert stays until the update succeeds and the notice is gone.
 */
function UpdateAlert({ instance, update }: { instance: InstanceSummary; update: Update }) {
  const notice = instance.notices.find((candidate) => UPDATE_ANSWERS.includes(candidate.code))
  const { updating } = update
  const failed = updating?.state === "failed" || updating?.state === "blocked"
  if (notice === undefined && !update.busy && !failed) return null
  return (
    <Alert variant={failed ? "destructive" : "default"}>
      {failed ? <CircleXIcon /> : <DownloadIcon />}
      <AlertTitle>
        {notice === undefined
          ? "Core update"
          : notice.code === "revision_behind"
            ? `Core ${notice.hub_revision.slice(0, 7)} is available`
            : noticeTitle(notice)}
      </AlertTitle>
      <AlertDescription>
        {notice !== undefined && <p>{notice.message}</p>}
        {updating !== undefined && <OperationSteps label="Update steps" steps={updating.steps} />}
        {updating?.state === "failed" && <p>The update failed: {updating.detail}</p>}
        {updating?.state === "blocked" && (
          <>
            <p>
              The update stopped before it began. The instance keeps running on its current core.
            </p>
            <ul aria-label="Problems" className="list-disc pl-4">
              {updating.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </>
        )}
      </AlertDescription>
      <AlertAction>
        <Button
          size="sm"
          disabled={update.busy}
          // The hub builds its own checkout from HEAD, which is the revision a notice names.
          onClick={() =>
            update.ask(notice?.code === "revision_behind" ? notice.hub_revision : "HEAD")
          }
        >
          {update.busy && <Spinner data-icon="inline-start" />}
          Update
        </Button>
      </AlertAction>
    </Alert>
  )
}

function Failure({
  title,
  detail,
  children,
}: {
  title: string
  detail: string
  /** What to do next. */
  children: ReactNode
}) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{detail}</p>
        <p>{children}</p>
      </AlertDescription>
    </Alert>
  )
}
