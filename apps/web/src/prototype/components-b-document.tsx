// PROTOTYPE variant B, "Document": no bubbles and no cards. A turn reads like a page: your request
// is its title, the reply is prose, the tool calls fold into one line you can open. Space groups,
// not lines; a single left rule marks the one thing waiting on you.
import {
  ArrowRightIcon,
  BrainIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleStopIcon,
  XIcon,
} from "lucide-react"
import { useState } from "react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"

import { approval, instances, tiles, turns, type ProtoTurn } from "./fixtures"
import { initial, Sheet, Specimen, STATE } from "./sheet"

export const name = "Document"

export function ComponentsB() {
  return (
    <Sheet
      title="Document"
      intent="The thread as a document. Each turn is titled by what you asked, the reply is long-form prose at reading width, and tool calls collapse into one summary line. Structure comes from type and space, not from containers."
    >
      <Specimen name="Turn" note="Routine run, then a turn you started">
        <div className="flex flex-col gap-14">
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Approval" note="Inline, where the turn stopped">
        <Approval />
      </Specimen>
      <Specimen name="Composer" note="Idle, then while a turn runs">
        <div className="flex max-w-prose flex-col gap-10">
          <Composer running={false} />
          <Composer running />
        </div>
      </Specimen>
      <Specimen name="Instance rows">
        <nav aria-label="Instances" className="w-72">
          <ul className="flex flex-col gap-1">
            {instances.map((instance, index) => {
              const state = STATE[instance.state]
              return (
                <li key={instance.id}>
                  <a
                    href="#b"
                    aria-current={index === 0 ? "page" : undefined}
                    className="flex items-center gap-3 rounded-lg px-2 py-2 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
                  >
                    <Avatar shape={instance.avatar.shape} size="sm" aria-hidden>
                      <AvatarFallback variant={instance.avatar.color}>
                        {initial(instance.name)}
                      </AvatarFallback>
                    </Avatar>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{instance.name}</span>
                      <span className="truncate text-xs text-muted-foreground">
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
      </Specimen>
      <Specimen name="Figures" note="No tiles: a line of figures">
        <dl className="flex flex-wrap gap-x-16 gap-y-8">
          {tiles.map((tile) => (
            <div key={tile.title} className="flex flex-col gap-1">
              <dt className="text-sm text-muted-foreground">{tile.title}</dt>
              <dd className="font-heading text-4xl font-semibold tabular-nums">{tile.value}</dd>
              <dd className="text-sm text-muted-foreground">{tile.note}</dd>
            </div>
          ))}
        </dl>
      </Specimen>
      <Specimen name="Empty state">
        <div className="flex max-w-prose flex-col items-start gap-4 py-8">
          <p className="font-heading text-2xl font-semibold">Nothing written here yet.</p>
          <p className="text-muted-foreground">
            Ask Atlas for something below. Every turn lands on this page, newest last.
          </p>
          <Button variant="outline">
            Start a thread
            <ArrowRightIcon data-icon="inline-end" />
          </Button>
        </div>
      </Specimen>
    </Sheet>
  )
}

function Turn({ turn }: { turn: ProtoTurn }) {
  const tools = turn.steps.filter((step) => step.kind === "tool")
  const text = turn.steps.filter((step) => step.kind === "text")
  const ms = tools.reduce((sum, step) => sum + (step.ms ?? 0), 0)
  const routine = turn.startedBy.kind === "routine"
  return (
    <article className="flex max-w-prose flex-col gap-4">
      <header className="flex flex-col gap-1">
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          {routine && <span aria-hidden className="size-2 rounded-full bg-ember" />}
          {turn.startedBy.kind === "routine"
            ? `Atlas, on its own · routine ${turn.startedBy.name}`
            : "You asked"}{" "}
          · {turn.at}
        </p>
        <h3 className="text-xl font-semibold">{turn.request}</h3>
      </header>
      <details className="group text-sm text-muted-foreground">
        <summary className="flex w-fit cursor-pointer list-none items-center gap-1 rounded-md outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
          <ChevronRightIcon className="size-4 transition-transform group-open:rotate-90" />
          {tools.length} {tools.length === 1 ? "step" : "steps"}
          {ms > 0 && ` · ${(ms / 1000).toFixed(1)} s`}
          {tools.some((step) => step.status === "waiting") && " · one waits for you"}
        </summary>
        <ol className="mt-2 flex flex-col gap-1 border-l pl-4">
          {tools.map((step) => (
            <li key={step.name}>
              <span className="font-mono text-xs text-foreground">{step.name}</span> {step.target}
              {" · "}
              {step.status === "waiting"
                ? "waiting for you"
                : step.status === "approved"
                  ? `approved by you, ${step.ms} ms`
                  : `${step.ms} ms`}
            </li>
          ))}
        </ol>
      </details>
      {text.map((step, index) => (
        <p key={index} className="text-base leading-relaxed">
          {step.text}
        </p>
      ))}
      <footer className="flex items-center gap-3 text-xs text-muted-foreground">
        {turn.end?.kind === "done" ? (
          <>
            <span className="flex items-center gap-1">
              <CheckIcon className="size-3.5" /> Done · {turn.end.tokens.toLocaleString("en")}{" "}
              tokens
            </span>
            {turn.recap === "episode" && (
              <a
                href="#b"
                className="flex items-center gap-1 underline underline-offset-3 hover:text-foreground"
              >
                <BrainIcon className="size-3.5" /> Recapped
              </a>
            )}
          </>
        ) : (
          <span className="flex items-center gap-1">
            <Spinner className="size-3.5" /> Working
          </span>
        )}
      </footer>
    </article>
  )
}

function Approval() {
  const [denying, setDenying] = useState(false)
  return (
    <section
      aria-labelledby="b-approval"
      className="flex max-w-prose flex-col gap-4 border-l-2 border-kin py-1 pl-5"
    >
      <div className="flex flex-col gap-1">
        <p className="text-xs text-muted-foreground">Waiting for you · rule {approval.rule}</p>
        <h3 id="b-approval" className="text-xl font-semibold">
          Send this email to Ana?
        </h3>
      </div>
      <dl className="grid grid-cols-4 gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">To</dt>
        <dd className="col-span-3">{approval.arguments.to}</dd>
        <dt className="text-muted-foreground">Subject</dt>
        <dd className="col-span-3">{approval.arguments.subject}</dd>
        <dt className="text-muted-foreground">Body</dt>
        <dd className="col-span-3 leading-relaxed">{approval.arguments.body}</dd>
      </dl>
      {denying && (
        <Field>
          <FieldLabel htmlFor="b-reason">What should Atlas do instead?</FieldLabel>
          <Input id="b-reason" placeholder="Optional" />
        </Field>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button>
          <CheckIcon data-icon="inline-start" />
          Send it
        </Button>
        <Button variant="outline" onClick={() => setDenying(true)}>
          <XIcon data-icon="inline-start" />
          {denying ? "Deny with this reason" : "Deny"}
        </Button>
        <Button variant="link" className="ml-auto">
          <CircleStopIcon data-icon="inline-start" />
          Stop the turn
        </Button>
      </div>
    </section>
  )
}

function Composer({ running }: { running: boolean }) {
  return (
    // The textarea drops its own ring; the rule above it turns kin while it has focus.
    <div className="flex flex-col gap-2 border-t-2 pt-4 focus-within:border-kin">
      <Textarea
        aria-label="Message Atlas"
        placeholder="Ask Atlas for something…"
        className="min-h-16 resize-none border-none bg-transparent px-0 text-base shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-muted-foreground">
          {running
            ? "Atlas is working on your last message"
            : "Enter to send · Shift+Enter for a new line"}
        </p>
        {running ? (
          <Button variant="outline" size="sm">
            <CircleStopIcon data-icon="inline-start" />
            Stop
          </Button>
        ) : (
          <Button size="sm">Send</Button>
        )}
      </div>
    </div>
  )
}
