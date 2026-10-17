// PROTOTYPE variant C, "Console": dense and inspectable. A turn is an event log on a rail, every
// row one event with its time cost right-aligned; ids and arguments in mono; figures in one strip.
// For the operator who wants to see what the agent did, not read a chat.
import {
  BrainIcon,
  CheckIcon,
  CircleStopIcon,
  CornerDownLeftIcon,
  MessageSquareIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  UserIcon,
  WrenchIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"

import { approval, instances, tiles, turns, type ProtoTurn } from "./fixtures"
import { Sheet, Specimen, STATE } from "./sheet"

export const name = "Console"

export function ComponentsC() {
  return (
    <Sheet
      title="Console"
      intent="Dense and inspectable. A turn is a log of events on a rail, each row one thing that happened with what it cost. Arguments stay visible as data. The register of a terminal, with the kinby palette."
    >
      <Specimen name="Turn" note="Routine run, then a turn you started">
        <div className="flex flex-col gap-6 rounded-lg border bg-card p-4">
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Approval" note="Data left, decision right">
        <section
          aria-labelledby="c-approval"
          className="grid overflow-hidden rounded-lg border md:grid-cols-5"
        >
          <div className="flex flex-col gap-2 bg-muted p-4 md:col-span-3">
            <p className="font-mono text-xs text-muted-foreground">arguments</p>
            <pre className="overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
          </div>
          <div className="flex flex-col gap-4 bg-card p-4 md:col-span-2">
            <div className="flex flex-col gap-2">
              <Badge variant="outline" className="font-mono">
                <ShieldAlertIcon />
                {approval.rule}
              </Badge>
              <h3 id="c-approval" className="font-mono text-sm font-semibold">
                {approval.call}
              </h3>
            </div>
            <Field>
              <FieldLabel htmlFor="c-reason">Reason, if you deny</FieldLabel>
              <Input id="c-reason" placeholder="what to do instead" />
            </Field>
            <div className="mt-auto grid grid-cols-2 gap-2">
              <Button>
                <CheckIcon data-icon="inline-start" />
                Approve
              </Button>
              <Button variant="outline">
                <XIcon data-icon="inline-start" />
                Deny
              </Button>
              <Button variant="ghost" className="col-span-2">
                <CircleStopIcon data-icon="inline-start" />
                Stop the turn
              </Button>
            </div>
          </div>
        </section>
      </Specimen>
      <Specimen name="Composer" note="Idle, then while a turn runs">
        <div className="flex flex-col gap-3">
          <Composer running={false} />
          <Composer running />
        </div>
      </Specimen>
      <Specimen name="Instance rows" note="One line each, state as a word">
        <table className="w-full max-w-xl text-sm">
          <caption className="sr-only">Instances</caption>
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="py-2 font-medium">Name</th>
              <th className="py-2 font-medium">State</th>
              <th className="py-2 text-right font-medium">Waiting</th>
            </tr>
          </thead>
          <tbody>
            {instances.map((instance, index) => {
              const state = STATE[instance.state]
              return (
                <tr key={instance.id} className="border-b last:border-0 hover:bg-accent">
                  <td className="py-2">
                    <a
                      href="#c"
                      aria-current={index === 0 ? "page" : undefined}
                      className="rounded-sm font-medium outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:text-kin"
                    >
                      {instance.name}
                    </a>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">
                      {instance.id}
                    </span>
                  </td>
                  <td className="py-2">
                    <span
                      className={
                        instance.state === "running"
                          ? "flex items-center gap-1.5 text-success"
                          : "flex items-center gap-1.5 text-muted-foreground"
                      }
                    >
                      <state.icon className="size-3.5" />
                      {state.label}
                    </span>
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {instance.needsYou > 0 ? (
                      <Badge variant="attention">{instance.needsYou} approval</Badge>
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Specimen>
      <Specimen name="Figures" note="One strip">
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
      </Specimen>
      <Specimen name="Empty state">
        <div className="flex flex-col gap-2 rounded-lg border border-dashed p-6 font-mono text-sm">
          <p>
            <span className="text-muted-foreground">$</span> threads list --instance atlas
          </p>
          <p className="text-muted-foreground">0 threads. Send a message to start one.</p>
        </div>
      </Specimen>
    </Sheet>
  )
}

function Turn({ turn }: { turn: ProtoTurn }) {
  const routine = turn.startedBy.kind === "routine"
  return (
    <ol className="flex flex-col" aria-label={`Turn ${turn.at}`}>
      <Row icon={routine ? RepeatIcon : UserIcon} tone={routine ? "ember" : "plain"} meta={turn.at}>
        <span className="font-medium">
          {turn.startedBy.kind === "routine" ? `routine · ${turn.startedBy.name}` : "you"}
        </span>
        <span className="text-muted-foreground"> — {turn.request}</span>
      </Row>
      {turn.steps.map((step, index) =>
        step.kind === "text" ? (
          <Row key={index} icon={MessageSquareIcon} meta="">
            <span className="leading-relaxed">{step.text}</span>
          </Row>
        ) : (
          <Row
            key={index}
            icon={step.status === "waiting" ? ShieldAlertIcon : WrenchIcon}
            tone={step.status === "waiting" ? "kin" : "plain"}
            meta={step.status === "waiting" ? "waiting" : `${step.ms} ms`}
          >
            <span className="font-mono text-xs">{step.name}</span>{" "}
            <span className="font-mono text-xs text-muted-foreground">{step.target}</span>
            {step.status === "approved" && (
              <span className="text-xs text-muted-foreground"> · approved by you</span>
            )}
          </Row>
        ),
      )}
      {turn.end?.kind === "done" ? (
        <Row icon={CheckIcon} meta={`${turn.end.tokens.toLocaleString("en")} tok`} last>
          <span className="text-muted-foreground">done</span>
          {turn.recap === "episode" && (
            <a
              href="#c"
              className="ml-3 inline-flex items-center gap-1 text-xs underline underline-offset-3"
            >
              <BrainIcon className="size-3" /> recap
            </a>
          )}
        </Row>
      ) : (
        <Row icon={null} meta="" last>
          <span className="flex items-center gap-2 text-muted-foreground">
            <Spinner className="size-3.5" /> working
          </span>
        </Row>
      )}
    </ol>
  )
}

function Row({
  icon: Icon,
  tone = "plain",
  meta,
  last,
  children,
}: {
  icon: LucideIcon | null
  tone?: "plain" | "kin" | "ember"
  meta: string
  last?: boolean
  children: ReactNode
}) {
  const node =
    tone === "ember"
      ? "bg-ember text-on-ember"
      : tone === "kin"
        ? "bg-primary text-primary-foreground"
        : "bg-muted text-muted-foreground"
  return (
    <li className="grid grid-cols-12 gap-3 text-sm">
      <div className="relative col-span-1 flex justify-center">
        {!last && <span aria-hidden className="absolute top-6 bottom-0 w-px bg-border" />}
        <span className={`mt-0.5 grid size-5 place-items-center rounded-full ${node}`}>
          {Icon && <Icon className="size-3" />}
        </span>
      </div>
      <div className="col-span-9 pb-3">{children}</div>
      <div className="col-span-2 pb-3 text-right font-mono text-xs text-muted-foreground tabular-nums">
        {meta}
      </div>
    </li>
  )
}

function Composer({ running }: { running: boolean }) {
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
