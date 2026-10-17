// PROTOTYPE variant E, "Merged": the picks from A–D in one sheet, for a last review.
// Type: Console's everywhere (Geist body, mono for ids, arguments, times and labels).
// Turn: Companion's speakers (avatars, your bubble, the agent's tinted bubble) with Console's rail
// for thinking and tool calls. Approval: Companion's. Composer and figures: Console's.
// Instance rows: Document's. Empty state: the one #753 ships, with its next step.
import {
  BrainIcon,
  CheckIcon,
  CirclePauseIcon,
  CircleStopIcon,
  CornerDownLeftIcon,
  LightbulbIcon,
  MailIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  WrenchIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"

import { approval, instances, tiles, turns, type ProtoTurn, type Step } from "./fixtures"
import { initial, Sheet, Specimen, STATE } from "./sheet"

export const name = "Merged picks"

const atlas = instances[0]

/** What the model thought before acting, which the other variants' fixtures leave out. */
const THINKING: Record<string, string> = {
  t1: "Calendar first, then unread mail from the last day. Flag anything that clashes.",
  t2: "Thursday 16:30 is free. Move it, then tell Ana the review can stay on Wednesday.",
}

export function ComponentsE() {
  return (
    <Sheet
      title="Merged picks"
      intent="Console type throughout. Turns keep Companion's speakers and bubbles, with Console's rail for thinking and tools. Companion's approval, Console's composer and figures, Document's instance rows, and the empty state from #753."
    >
      <Specimen name="Turn" note="Routine run, then a turn you started">
        <div className="flex max-w-3xl flex-col gap-10">
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Approval" note="Companion">
        <Approval />
      </Specimen>
      <Specimen name="Composer" note="Console · idle, then while a turn runs">
        <div className="flex max-w-3xl flex-col gap-3">
          <Composer running={false} />
          <Composer running />
        </div>
      </Specimen>
      <Specimen name="Instance rows" note="Document">
        <div className="w-72">
          <InstanceRows selected="atlas" />
        </div>
      </Specimen>
      <Specimen name="Figures" note="Console">
        <Figures />
      </Specimen>
      <Specimen name="Empty state" note="From #753: says what to do next">
        <NotRunning name="Ledger" what="threads" />
      </Specimen>
    </Sheet>
  )
}

export function Approval() {
  return (
    <section aria-labelledby="e-approval" className="flex max-w-3xl gap-3">
      <AtlasAvatar />
      <Card className="flex-1">
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h3 id="e-approval" className="text-lg font-semibold">
              Can I send this email to Ana?
            </h3>
            <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
              <ShieldAlertIcon className="size-3.5" />
              {approval.rule}
            </p>
          </div>
          <div className="flex flex-col gap-2 rounded-lg bg-muted p-4 text-sm">
            <p className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
              <MailIcon className="size-3.5" /> to {approval.arguments.to}
            </p>
            <p className="font-medium">{approval.arguments.subject}</p>
            <p className="leading-relaxed">{approval.arguments.body}</p>
          </div>
          <Field>
            <FieldLabel htmlFor="e-reason">Anything to change?</FieldLabel>
            <Input id="e-reason" placeholder="Say no and tell Atlas what to do instead" />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="lg">
              <CheckIcon data-icon="inline-start" />
              Yes, send it
            </Button>
            <Button size="lg" variant="outline">
              <XIcon data-icon="inline-start" />
              No
            </Button>
            <Button variant="link" className="ml-auto">
              <CircleStopIcon data-icon="inline-start" />
              Stop the turn
            </Button>
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

/** Document's rows: the name over what the instance is doing, an ember dot when it waits on you. */
export function InstanceRows({ selected }: { selected?: string }) {
  return (
    <nav aria-label="Instances">
      <ul className="flex flex-col gap-1">
        {instances.map((instance) => {
          const state = STATE[instance.state]
          return (
            <li key={instance.id}>
              <a
                href="#e"
                aria-current={instance.id === selected ? "page" : undefined}
                className="flex items-center gap-3 rounded-lg px-2 py-2 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
              >
                <Avatar shape={instance.avatar.shape} size="sm" aria-hidden>
                  <AvatarFallback variant={instance.avatar.color}>
                    {initial(instance.name)}
                  </AvatarFallback>
                </Avatar>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm font-medium">{instance.name}</span>
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {instance.needsYou > 0
                      ? `${instance.needsYou} waiting for you`
                      : instance.state === "running"
                        ? instance.role
                        : state.label}
                  </span>
                </span>
                {instance.needsYou > 0 && (
                  <span aria-hidden className="size-2 rounded-full bg-ember" />
                )}
              </a>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

export function Figures() {
  return (
    <dl className="grid overflow-hidden rounded-lg border sm:grid-cols-3 sm:divide-x">
      {tiles.map((tile) => (
        <div
          key={tile.title}
          className="flex flex-col gap-1 border-b p-4 last:border-0 sm:border-b-0"
        >
          <dt className="font-mono text-xs text-muted-foreground uppercase">{tile.title}</dt>
          <dd className="text-2xl font-semibold tabular-nums">{tile.value}</dd>
          <dd className="font-mono text-xs text-muted-foreground">{tile.note}</dd>
        </div>
      ))}
    </dl>
  )
}

/** #753's empty state: what is wrong, then the one step that fixes it. */
export function NotRunning({ name, what }: { name: string; what: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <CirclePauseIcon />
        </EmptyMedia>
        <EmptyTitle>{name} is not running</EmptyTitle>
        <EmptyDescription>Start it from its page to see its {what}.</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button>Open {name}</Button>
      </EmptyContent>
    </Empty>
  )
}

export function AtlasAvatar() {
  return (
    <Avatar shape={atlas.avatar.shape} aria-hidden>
      <AvatarFallback variant={atlas.avatar.color}>{initial(atlas.name)}</AvatarFallback>
    </Avatar>
  )
}

/** Consecutive thinking and tool steps share one rail; a text step breaks it with a bubble. */
type Run = { kind: "text"; text: string } | { kind: "rail"; steps: RailStep[] }
type RailStep = { kind: "thinking"; text: string } | Extract<Step, { kind: "tool" }>

function runs(turn: ProtoTurn): Run[] {
  const steps: (RailStep | Extract<Step, { kind: "text" }>)[] = [
    ...(THINKING[turn.id] ? [{ kind: "thinking" as const, text: THINKING[turn.id] }] : []),
    ...turn.steps,
  ]
  const out: Run[] = []
  for (const step of steps) {
    if (step.kind === "text") out.push(step)
    else {
      const last = out.at(-1)
      if (last?.kind === "rail") last.steps.push(step)
      else out.push({ kind: "rail", steps: [step] })
    }
  }
  return out
}

export function Turn({ turn }: { turn: ProtoTurn }) {
  const routine = turn.startedBy.kind === "routine"
  return (
    <article className="flex flex-col gap-4" aria-label={`Turn ${turn.at}`}>
      {turn.startedBy.kind === "routine" ? (
        <p className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
          <span className="grid size-5 place-items-center rounded-full bg-ember text-on-ember">
            <RepeatIcon className="size-3" />
          </span>
          routine · {turn.startedBy.name} · {turn.at}
        </p>
      ) : (
        <div className="flex items-end justify-end gap-3">
          <div className="flex flex-col items-end gap-1">
            <p className="font-mono text-xs text-muted-foreground">you · {turn.at}</p>
            <Bubble variant="default" align="end">
              <BubbleContent>{turn.request}</BubbleContent>
            </Bubble>
          </div>
          <Avatar size="sm" aria-hidden>
            <AvatarFallback>J</AvatarFallback>
          </Avatar>
        </div>
      )}
      {routine && <p className="pl-8 text-sm text-muted-foreground">{turn.request}</p>}
      <div className="flex gap-3">
        <AtlasAvatar />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="font-mono text-xs text-muted-foreground">atlas</p>
          {runs(turn).map((run, index) =>
            run.kind === "text" ? (
              <Bubble key={index} variant="tinted">
                <BubbleContent>{run.text}</BubbleContent>
              </Bubble>
            ) : (
              <ol key={index} className="flex flex-col">
                {run.steps.map((step, stepIndex) => (
                  <RailRow key={stepIndex} step={step} last={stepIndex === run.steps.length - 1} />
                ))}
              </ol>
            ),
          )}
          <p className="flex items-center gap-3 font-mono text-xs text-muted-foreground">
            {turn.end?.kind === "done" ? (
              <>
                <span className="flex items-center gap-1.5">
                  <CheckIcon className="size-3.5" /> done · {turn.end.tokens.toLocaleString("en")}{" "}
                  tok
                </span>
                {turn.recap === "episode" && (
                  <a
                    href="#e"
                    className="flex items-center gap-1 underline underline-offset-3 hover:text-foreground"
                  >
                    <BrainIcon className="size-3.5" /> recap
                  </a>
                )}
              </>
            ) : (
              <span className="flex items-center gap-1.5">
                <Spinner className="size-3.5" /> working
              </span>
            )}
          </p>
        </div>
      </div>
    </article>
  )
}

function RailRow({ step, last }: { step: RailStep; last: boolean }) {
  if (step.kind === "thinking") {
    return (
      <Row icon={LightbulbIcon} meta="" last={last}>
        <span className="text-muted-foreground">{step.text}</span>
      </Row>
    )
  }
  return (
    <Row
      icon={step.status === "waiting" ? ShieldAlertIcon : WrenchIcon}
      tone={step.status === "waiting" ? "kin" : "plain"}
      meta={step.status === "waiting" ? "waiting" : `${step.ms} ms`}
      last={last}
    >
      <span className="font-mono text-xs">{step.name}</span>{" "}
      <span className="font-mono text-xs text-muted-foreground">{step.target}</span>
      {step.status === "approved" && (
        <span className="text-xs text-muted-foreground"> · approved by you</span>
      )}
      {step.status === "waiting" && (
        <span className="text-xs text-muted-foreground"> · needs your answer</span>
      )}
    </Row>
  )
}

function Row({
  icon: Icon,
  tone = "plain",
  meta,
  last,
  children,
}: {
  icon: LucideIcon
  tone?: "plain" | "kin"
  meta: string
  last: boolean
  children: ReactNode
}) {
  return (
    <li className="flex gap-3 text-sm">
      <div className="relative flex w-5 shrink-0 justify-center">
        {!last && <span aria-hidden className="absolute top-6 bottom-0 w-px bg-border" />}
        <span
          className={
            tone === "kin"
              ? "mt-0.5 grid size-5 place-items-center rounded-full bg-primary text-primary-foreground"
              : "mt-0.5 grid size-5 place-items-center rounded-full bg-muted text-muted-foreground"
          }
        >
          <Icon className="size-3" />
        </span>
      </div>
      <div className="min-w-0 flex-1 pb-3">{children}</div>
      <div className="shrink-0 pb-3 text-right font-mono text-xs text-muted-foreground tabular-nums">
        {meta}
      </div>
    </li>
  )
}

export function Composer({ running }: { running: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-input bg-card px-3 py-1.5 focus-within:ring-3 focus-within:ring-ring/50">
      <span aria-hidden className="font-mono text-kin">
        ›
      </span>
      <input
        aria-label="Message Atlas"
        placeholder={running ? "Atlas is working…" : "message atlas, or /routine, /memory"}
        className="h-8 min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-muted-foreground"
      />
      {running ? (
        <Button variant="outline" size="sm">
          <SquareIcon data-icon="inline-start" className="fill-current" />
          Stop
          <kbd className="ml-1 font-mono text-xs text-muted-foreground">Esc</kbd>
        </Button>
      ) : (
        <Button size="sm">
          Send
          <CornerDownLeftIcon data-icon="inline-end" />
        </Button>
      )}
    </div>
  )
}
