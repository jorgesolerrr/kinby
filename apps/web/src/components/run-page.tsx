import type {
  Client,
  Clock,
  FactoryRun,
  FactoryRunDetail,
  StepAttempt,
  StepEnding,
  WaitingApproval,
  Workbench,
} from "@kinby/contract"
import {
  BotIcon,
  CodeIcon,
  FlagIcon,
  GlobeIcon,
  HandIcon,
  TerminalIcon,
  TimerIcon,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { PageLink } from "@/components/factories-page"
import { LiveBrowser, type LiveBrowserInput } from "@/components/live-browser"
import { PageColumn } from "@/components/page-column"
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Item, ItemActions, ItemContent, ItemMedia, ItemTitle } from "@/components/ui/item"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"
import { when } from "@/lib/config-changes"
import { finished, STATUS_BADGES, standing, workItem } from "@/lib/factory-runs"
import { type FactorySteps, lineName, readRun, stepKind, type Wait, waitingOn } from "@/lib/lines"
import { type Entry, readTranscript } from "@/lib/run-transcript"
import { FACTORIES_PATH, linePath } from "@/lib/selection"
import { duration, money, shortTokens } from "@/lib/stats"
import { mainArgument } from "@/lib/timeline"

type Hub = Pick<Client, "call" | "subscribe">
type Step = FactorySteps["steps"][number]

/** How often the page reads again what is still moving: the run, and an attempt not yet ended. */
const FOLLOW_MS = 5_000

const ENDINGS: Record<
  StepEnding,
  { label: string; variant: "outline" | "destructive" | "attention" | "secondary" | "success" }
> = {
  clean: { label: "Clean", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
  "timed-out": { label: "Timed out", variant: "destructive" },
  interrupted: { label: "Interrupted", variant: "secondary" },
  "needs-human": { label: "Needs a human", variant: "attention" },
  limited: { label: "Limited", variant: "secondary" },
  asked: { label: "Asked", variant: "attention" },
}

const STEP_ICONS: Record<string, typeof BotIcon> = {
  agent: BotIcon,
  command: TerminalIcon,
  code: CodeIcon,
  wait: TimerIcon,
  approve: HandIcon,
}

/**
 * One run of a line: a row per step of its factory, in order, with the step's kind and engine, its
 * attempts and how the last one ended. The step the run is at starts open. A step's attempts sit
 * under it in order, each with its session, time, cost, summary and, for an agent step, its
 * transcript. When the run waits on the user, a card at the top says for what and takes the answer.
 * The page reads the run again while it moves.
 */
export function RunPage({ hub, clock, runId }: { hub: Hub; clock: Clock; runId: string }) {
  const read = useCallback(() => readRun(hub, runId), [hub, runId])
  const {
    value: opened,
    failure,
    readAgain,
  } = useFollowed(read, clock, ({ detail }) => !finished(detail.run))
  if (opened === undefined) {
    return (
      <PageColumn className="py-6">
        {failure === undefined ? <Skeleton className="h-72 w-full" /> : <Failure error={failure} />}
      </PageColumn>
    )
  }
  const { detail, factory, line, workbench } = opened
  const { run, attempts } = detail
  const wait = waitingOn(run, stepKind(factory, run), attempts.at(-1))
  return (
    <PageColumn className="flex flex-col gap-6 py-6">
      <div className="flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">
          <PageLink href={FACTORIES_PATH} className="hover:underline">
            Factories
          </PageLink>{" "}
          /{" "}
          <PageLink href={linePath(line.line_id)} className="hover:underline">
            {lineName(line, [workbench])}
          </PageLink>
        </p>
        <h1 className="font-heading text-2xl font-semibold">{workItem(run)}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={STATUS_BADGES[run.status]}>{standing(run)}</Badge>
          started {when(run.created_at)}
        </p>
      </div>
      {failure !== undefined && <Failure error={failure} />}
      {wait !== undefined && (
        <Waiting hub={hub} detail={detail} wait={wait} workbench={workbench} onActed={readAgain} />
      )}
      <ol aria-label="Steps" className="flex flex-col gap-2">
        {factory.steps.map((step) => (
          <StepRow
            key={step.id}
            hub={hub}
            clock={clock}
            run={run}
            step={step}
            attempts={attempts.filter((attempt) => attempt.step === step.id)}
          />
        ))}
      </ol>
    </PageColumn>
  )
}

/** What `read` returns, read again every few seconds while `moving` says what it returned moves. */
function useFollowed<T>(read: () => Promise<T>, clock: Clock, moving: (value: T) => boolean) {
  const followed = useRead(read, clock)
  const { value, failure, readAgain } = followed
  const follow = value !== undefined && moving(value)
  useEffect(
    () => (follow ? clock.after(FOLLOW_MS, readAgain) : undefined),
    // Each read, failed or not, sets the next one.
    [follow, value, failure, clock, readAgain],
  )
  return followed
}

function StepRow({
  hub,
  clock,
  run,
  step,
  attempts,
}: {
  hub: Hub
  clock: Clock
  run: FactoryRun
  step: Step
  attempts: StepAttempt[]
}) {
  const Icon = STEP_ICONS[step.kind] ?? CodeIcon
  const last = attempts.at(-1)
  const kind = step.engine === undefined ? step.kind : `${step.kind} · ${step.engine}`
  const tries =
    attempts.length === 0
      ? "Not reached"
      : `${attempts.length} ${attempts.length === 1 ? "attempt" : "attempts"}`
  const ending = last === undefined ? undefined : endingOf(last.ending)
  return (
    <Collapsible defaultOpen={run.step === step.id} render={<li />}>
      <CollapsibleTrigger
        disabled={attempts.length === 0}
        render={
          <Item
            variant="outline"
            size="sm"
            render={
              <button
                type="button"
                aria-label={[step.id, kind, tries, ending?.label].filter(Boolean).join(", ")}
              />
            }
          />
        }
      >
        <ItemMedia variant="icon">
          <Icon className="text-muted-foreground" />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>
            {step.id}
            <span className="font-normal text-muted-foreground">{kind}</span>
          </ItemTitle>
        </ItemContent>
        <ItemActions>
          <span className="text-sm text-muted-foreground">{tries}</span>
          {ending !== undefined && <Badge variant={ending.variant}>{ending.label}</Badge>}
        </ItemActions>
      </CollapsibleTrigger>
      <CollapsibleContent render={<section aria-label={step.id} />}>
        <div className="ml-6 flex flex-col gap-4 border-l-2 py-3 pl-6">
          {attempts.map((attempt) => (
            <Attempt
              key={attempt.attempt}
              hub={hub}
              clock={clock}
              runId={run.run_id}
              step={step}
              attempt={attempt}
            />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/** How an attempt ended, or Running while it has not. */
function endingOf(ending: StepEnding | null): (typeof ENDINGS)[StepEnding] {
  return ending === null ? { label: "Running", variant: "success" } : ENDINGS[ending]
}

/**
 * One attempt: its number, session, start, how long it took and what it spent, then its summary.
 * A command step's summary holds the exit and the tail of a failing command's output, and an agent
 * step's attempt shows its transcript.
 */
function Attempt({
  hub,
  clock,
  runId,
  step,
  attempt,
}: {
  hub: Hub
  clock: Clock
  runId: string
  step: Step
  attempt: StepAttempt
}) {
  const { started_at, ended_at, session, summary, usage } = attempt
  const delegated = usage?.delegated_run
  const facts = [
    `Attempt ${attempt.attempt}`,
    session,
    when(started_at),
    ended_at !== null && duration(Date.parse(ended_at) - Date.parse(started_at)),
    usage?.api?.cost != null && money(usage.api.cost),
    delegated != null &&
      `${shortTokens(delegated.input_tokens + delegated.output_tokens)} tokens on ${delegated.client}`,
  ]
  return (
    <article className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">{facts.filter(Boolean).join(" · ")}</p>
      {summary !== "" &&
        (step.kind === "command" ? (
          <pre className="rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
            {summary}
          </pre>
        ) : (
          <p className="text-sm font-medium whitespace-pre-wrap">{summary}</p>
        ))}
      {step.kind === "agent" && (
        <Transcript hub={hub} clock={clock} runId={runId} attempt={attempt} />
      )}
    </article>
  )
}

/** An agent step's attempt as its engine streamed it, read again while the attempt runs. */
function Transcript({
  hub,
  clock,
  runId,
  attempt: { step, attempt, ended_at },
}: {
  hub: Hub
  clock: Clock
  runId: string
  attempt: StepAttempt
}) {
  const read = useCallback(
    () => readTranscript(hub, runId, step, attempt),
    [hub, runId, step, attempt],
  )
  const ended = ended_at !== null
  const { value: entries, readAgain } = useFollowed(read, clock, () => !ended)
  // Ending cancels the next read, which may hold the attempt's last words.
  const endedBefore = useRef(ended)
  useEffect(() => {
    if (ended && !endedBefore.current) readAgain()
    endedBefore.current = ended
  }, [ended, readAgain])
  if (entries === undefined || entries.length === 0) return null
  return (
    <ol className="flex flex-col gap-1.5 text-sm">
      {entries.map((entry, index) => (
        // The transcript only grows, so an entry keeps its place.
        <li key={index}>
          <TranscriptEntry entry={entry} />
        </li>
      ))}
    </ol>
  )
}

function TranscriptEntry({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "say":
      return <p className="whitespace-pre-wrap">{entry.text}</p>
    case "tool":
      return (
        <p className="font-mono text-xs text-muted-foreground">
          <span className="text-foreground">{entry.name}</span> <span>{entry.text}</span>
        </p>
      )
    case "command":
      return (
        <p className="font-mono text-xs text-muted-foreground">
          <span className={entry.exit === 0 ? "text-success" : "text-destructive"}>
            {entry.exit === null ? "…" : entry.exit === 0 ? "✓" : `✗ exit ${entry.exit}`}
          </span>{" "}
          $ {entry.text}
        </p>
      )
    case "cut":
      return (
        <p className="text-xs text-muted-foreground">
          An event of {shortTokens(entry.bytes)} bytes, too large to show.
        </p>
      )
  }
}

/**
 * What the run waits on the user for, and the answers it takes. A run that needs a human retries,
 * goes back to an earlier step with notes, or is cancelled. One at an approve step is approved or
 * sent back. An agent step's ask is approved or denied, and its takeover opens the workbench's
 * browser here, where Finish resumes the turn.
 */
function Waiting({
  hub,
  detail: { run, attempts, earlier_steps },
  wait,
  workbench,
  onActed,
}: {
  hub: Hub
  detail: FactoryRunDetail
  wait: Wait
  workbench: Workbench
  onActed: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const { run_id } = run
  const step = run.step ?? ""
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(undefined)
    try {
      await work()
      onActed()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }
  const sendBack = (
    <SendBack
      earlier={earlier_steps}
      busy={busy}
      onSend={(to, notes) =>
        act(() => hub.call("factory.run.send_back", { run_id, step: to, notes }))
      }
    />
  )
  const summary = attempts.at(-1)?.summary
  let title: string
  let description: string | undefined
  let actions: ReactNode
  switch (wait.kind) {
    case "needs-human":
      title = `Needs a human at ${step}`
      description = summary
      actions = (
        <>
          <Button
            disabled={busy}
            onClick={() => void act(() => hub.call("factory.run.retry", { run_id }))}
          >
            Retry {step}
          </Button>
          {sendBack}
          <CancelRun
            run={run}
            busy={busy}
            onCancel={() => act(() => hub.call("factory.run.cancel", { run_id }))}
          />
        </>
      )
      break
    case "approval":
      title = `${step} waits for your approval`
      description = summary
      actions = (
        <>
          <Button
            disabled={busy}
            onClick={() => void act(() => hub.call("factory.run.approve", { run_id }))}
          >
            Approve
          </Button>
          {sendBack}
        </>
      )
      break
    case "ask": {
      const { request, turn_id } = wait.ask
      if (request.type === "takeover.requested") {
        title = `${step} needs you to take over the browser`
        description = request.reason
        actions = (
          <Takeover
            hub={hub}
            pageOf={{ instance_id: workbench.workbench_id, turn_id }}
            busy={busy}
            onFinish={(drained) =>
              act(async () => {
                // What the user typed last reaches the page before the turn resumes.
                await drained()
                return hub.call("factory.run.takeover.finish", {
                  run_id,
                  takeover_id: request.takeover_id,
                })
              })
            }
          />
        )
      } else {
        title = `${step} asks to call ${request.name}`
        description = askedFor(request)
        const answer = (decision: "approve" | "deny") =>
          act(() =>
            hub.call("factory.run.answer", {
              run_id,
              approval_id: request.approval_id,
              answer: { decision },
            }),
          )
        actions = (
          <>
            <Button disabled={busy} onClick={() => void answer("approve")}>
              Approve
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => void answer("deny")}>
              Deny
            </Button>
          </>
        )
      }
      break
    }
  }
  return (
    <section aria-label="Waiting on you">
      <Card variant="attention">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {description !== undefined && description !== "" && (
            <CardDescription className="wrap-anywhere whitespace-pre-wrap">
              {description}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">{actions}</div>
            {failure !== undefined && <Failure error={failure} />}
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

/** What a call asks to do: the preview its tool gives, or the thing it acts on. */
function askedFor(request: WaitingApproval["request"]): string | undefined {
  return request.preview ?? mainArgument(request.arguments)
}

/** Send the run back to an earlier step, the latest by default, with notes for it or none. */
function SendBack({
  earlier,
  busy,
  onSend,
}: {
  earlier: string[]
  busy: boolean
  onSend: (step: string, notes: string | null) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(earlier.at(-1))
  const [notes, setNotes] = useState("")
  const stepId = useId()
  const notesId = useId()
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" disabled={busy || earlier.length === 0} />}>
        Send back…
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send the run back</DialogTitle>
          <DialogDescription>
            The step runs again with your notes. Its retries and send-backs count afresh.
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor={stepId}>Step</FieldLabel>
            <Select
              items={earlier.map((each) => ({ value: each, label: each }))}
              value={step ?? null}
              onValueChange={(picked) => typeof picked === "string" && setStep(picked)}
            >
              <SelectTrigger id={stepId} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {earlier.map((each) => (
                  <SelectItem key={each} value={each}>
                    {each}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor={notesId}>Notes</FieldLabel>
            <Textarea
              id={notesId}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </Field>
        </FieldGroup>
        <DialogFooter>
          <Button
            disabled={busy || step === undefined}
            onClick={() => {
              if (step === undefined) return
              setOpen(false)
              void onSend(step, notes.trim() === "" ? null : notes.trim())
            }}
          >
            Send back
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CancelRun({
  run,
  busy,
  onCancel,
}: {
  run: FactoryRun
  busy: boolean
  onCancel: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger render={<Button variant="ghost" disabled={busy} />}>
        Cancel run
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Cancel the run of {workItem(run)}?</AlertDialogTitle>
          <AlertDialogDescription>
            It ends where it stopped, keeping what it did.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep it</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => {
              setOpen(false)
              void onCancel()
            }}
          >
            Cancel the run
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * The takeover an agent step's turn asks for: Take over opens the page the turn left in the
 * workbench's browser, live, and Finish resumes the turn with the page as the user left it.
 */
function Takeover({
  hub,
  pageOf,
  busy,
  onFinish,
}: {
  hub: Hub
  /** The workbench and the turn whose page it is. */
  pageOf: { instance_id: string; turn_id: string }
  busy: boolean
  onFinish: (drained: () => Promise<void>) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const live = useRef<LiveBrowserInput>(null)
  const { instance_id, turn_id } = pageOf
  const watch = useCallback(
    () => hub.subscribe("thread.takeover.start", { instance_id, turn_id }),
    [hub, instance_id, turn_id],
  )
  if (!open) {
    return (
      <Button onClick={() => setOpen(true)}>
        <GlobeIcon data-icon="inline-start" />
        Take over
      </Button>
    )
  }
  return (
    <div className="flex w-full flex-col gap-2">
      <LiveBrowser
        ref={live}
        className="min-h-72"
        watch={watch}
        send={(input) => hub.call("thread.takeover.input", { instance_id, turn_id, input })}
        // Input after Finish would reach a turn that already moved on.
        disabled={busy}
      />
      <Button
        className="self-end"
        disabled={busy}
        onClick={() => void onFinish(() => live.current?.drained() ?? Promise.resolve())}
      >
        {busy ? <Spinner data-icon="inline-start" /> : <FlagIcon data-icon="inline-start" />}
        Finish
      </Button>
    </div>
  )
}
