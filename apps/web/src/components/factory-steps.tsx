import { cn } from "cn"
import { CircleXIcon, PlusIcon } from "lucide-react"
import { useId, useState } from "react"

import { NewStep } from "@/components/new-step"
import { StepEditor, type StepSaving } from "@/components/step-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { type FactoryStep, routeText } from "@/lib/factory-steps"

/** A route between two steps, by their places in the factory, `to` the earlier one. */
interface Arc {
  from: number
  to: number
  title: string
  label?: string
  failed?: boolean
}

const GAP = 96
const PAD = 48
const NODE = 12
const LANE = 20
const RESUME_LANE = 8

/** The factory's steps as a map, its routes back as arcs over the line and its resumed sessions
 * as dotted arcs under it, above a card for each step, which opens in place as its step editor.
 * A + after each card adds a step there. `onSaved` runs once a change made a new version. */
export function FactorySteps({
  steps,
  commands,
  saving,
  onSaved,
}: {
  steps: FactoryStep[]
  /** The names of the workbench's command lists the factory requires. */
  commands: string[]
  saving: StepSaving
  onSaved: () => void
}) {
  const prefix = useId()
  const cardId = (id: string) => `${prefix}${id}`
  const [open, setOpen] = useState<string>()
  /** The step the user adds a new one after. */
  const [adding, setAdding] = useState<string>()
  /** The step the hub refused to turn off or on, and what each step that keeps it needs of it. */
  const [kept, setKept] = useState<{ step: string; needs: Record<string, string> }>()
  const pick = (id: string) => {
    setOpen(id)
    document.getElementById(cardId(id))?.scrollIntoView({ behavior: "smooth", block: "start" })
  }
  const changed = () => {
    setKept(undefined)
    onSaved()
  }
  return (
    <div className="flex flex-col gap-4">
      <FactoryMap
        steps={steps}
        cardId={cardId}
        blocking={Object.keys(kept?.needs ?? {})}
        onPick={pick}
      />
      <ol aria-label="Steps" className="flex flex-col">
        {steps.map((step, place) => (
          <li key={step.id} id={cardId(step.id)} className="scroll-mt-24">
            <StepCard
              step={step}
              open={open === step.id}
              onToggle={() => setOpen(open === step.id ? undefined : step.id)}
            >
              {kept?.step === step.id && Object.keys(kept.needs).length > 0 && (
                <KeptAlert step={step} needs={kept.needs} cardId={cardId} onPick={pick} />
              )}
              <StepEditor
                // A toggle leaves the card open, on the factory as it reads now.
                key={saving.factory.hash}
                step={step}
                earlier={steps.slice(0, place).filter((each) => !each.off)}
                saving={saving}
                onSaved={() => {
                  setOpen(undefined)
                  changed()
                }}
                onTurned={changed}
                onKept={(needs) => setKept({ step: step.id, needs })}
              />
            </StepCard>
            {adding === step.id ? (
              <div className="pt-3">
                <NewStep
                  key={saving.factory.hash}
                  after={step.id}
                  steps={steps}
                  commands={commands}
                  saving={saving}
                  onAdded={(id) => {
                    setAdding(undefined)
                    setOpen(id)
                    changed()
                  }}
                  onCancel={() => setAdding(undefined)}
                />
              </div>
            ) : (
              <div className="flex justify-center pt-3">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Add a step after ${step.id}`}
                  onClick={() => setAdding(step.id)}
                >
                  <PlusIcon />
                </Button>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Why the hub keeps `step` off or on: each step that needs it as it is, as a link to its card. */
function KeptAlert({
  step,
  needs,
  cardId,
  onPick,
}: {
  step: FactoryStep
  needs: Record<string, string>
  cardId: (id: string) => string
  onPick: (id: string) => void
}) {
  const blocking = Object.entries(needs)
  return (
    <Alert variant="destructive" className="mb-4">
      <CircleXIcon />
      <AlertTitle>
        The factory check keeps {step.id} {step.off ? "off" : "on"}
      </AlertTitle>
      <AlertDescription>
        <ul className="list-disc pl-4">
          {blocking.map(([id, why]) => (
            <li key={id}>
              <a
                href={`#${cardId(id)}`}
                className="font-mono font-medium"
                onClick={(event) => {
                  event.preventDefault()
                  onPick(id)
                }}
              >
                {id}
              </a>{" "}
              {why}
            </li>
          ))}
        </ul>
        Change {blocking.length === 1 ? "that step" : "those steps"} first.
      </AlertDescription>
    </Alert>
  )
}

function FactoryMap({
  steps,
  cardId,
  blocking,
  onPick,
}: {
  steps: FactoryStep[]
  cardId: (id: string) => string
  /** The steps that keep a step the user turned off or on as it is, whose nodes pulse. */
  blocking: string[]
  onPick: (id: string) => void
}) {
  const at = (id: string) => steps.findIndex((step) => step.id === id)
  const back = laned(steps.flatMap((step, from) => routesBack(step, from, at)))
  const resumed = laned(
    steps.flatMap((step, from) =>
      step.resume == null
        ? []
        : [{ from, to: at(step.resume), title: `${step.id} resumes ${step.resume}'s session` }],
    ),
  )
  const lanes = (arcs: { lane: number }[]) => Math.max(0, ...arcs.map((arc) => arc.lane))
  const line = 16 + lanes(back) * LANE + NODE
  const below = line + NODE + lanes(resumed) * RESUME_LANE
  const width = PAD * 2 + (steps.length - 1) * GAP
  const height = below + 40
  const x = (place: number) => PAD + place * GAP

  return (
    <figure aria-label="Map" className="overflow-x-auto rounded-xl border bg-card">
      <svg width={width} height={height} className="block">
        <line
          x1={x(0)}
          x2={x(steps.length - 1)}
          y1={line}
          y2={line}
          strokeWidth={3}
          className="stroke-foreground/70"
        />
        <g aria-label="Routes back">
          {back.map((arc) => {
            const rise = arc.lane * LANE
            return (
              <g key={arc.title}>
                <path
                  d={curve(x(arc.from), x(arc.to), line - NODE, -rise)}
                  fill="none"
                  strokeWidth={1.5}
                  className={arc.failed ? "stroke-destructive" : "stroke-primary"}
                >
                  <title>{arc.title}</title>
                </path>
                <text
                  x={(x(arc.from) + x(arc.to)) / 2}
                  y={line - NODE - rise - 4}
                  textAnchor="middle"
                  fontSize={10}
                  className={arc.failed ? "fill-destructive" : "fill-primary"}
                >
                  {arc.label}
                </text>
              </g>
            )
          })}
        </g>
        <g aria-label="Resumed sessions">
          {resumed.map((arc) => (
            <path
              key={arc.title}
              d={curve(x(arc.from), x(arc.to), line + NODE, arc.lane * RESUME_LANE)}
              fill="none"
              strokeDasharray="2 3"
              strokeWidth={1.5}
              className="stroke-muted-foreground"
            >
              <title>{arc.title}</title>
            </path>
          ))}
        </g>
        {steps.map((step, place) => (
          <a
            key={step.id}
            href={`#${cardId(step.id)}`}
            aria-label={step.id}
            onClick={(event) => {
              event.preventDefault()
              onPick(step.id)
            }}
            className="group"
          >
            <circle
              cx={x(place)}
              cy={line}
              r={NODE - 2}
              strokeWidth={3}
              strokeDasharray={step.off ? "3 3" : undefined}
              className={cn(
                "group-hover:stroke-primary",
                blocking.includes(step.id)
                  ? "animate-pulse fill-destructive stroke-destructive"
                  : step.off
                    ? "fill-background stroke-muted-foreground"
                    : "fill-background stroke-foreground",
              )}
            />
            <text
              x={x(place)}
              y={below + 16}
              textAnchor="middle"
              fontSize={11}
              className={cn(
                "font-mono",
                step.off ? "fill-muted-foreground line-through" : "fill-foreground",
              )}
            >
              {step.id}
            </text>
            <text
              x={x(place)}
              y={below + 30}
              textAnchor="middle"
              fontSize={10}
              className="fill-muted-foreground"
            >
              {step.kind === "agent" ? (step.model ?? step.engine) : step.kind}
            </text>
          </a>
        ))}
      </svg>
    </figure>
  )
}

/** The step's outcomes and failure that send the run back, each an arc labelled with its max. */
function routesBack(step: FactoryStep, from: number, at: (id: string) => number): Arc[] {
  const routes = Object.entries(step.outcomes ?? {}).map(([outcome, target]) => ({
    outcome,
    target: typeof target === "object" && "back" in target ? target : undefined,
    failed: false,
  }))
  if (step.failed != null && "back" in step.failed) {
    routes.push({ outcome: "failed", target: step.failed, failed: true })
  }
  return routes.flatMap(({ outcome, target, failed }) =>
    target === undefined
      ? []
      : [
          {
            from,
            to: at(target.back),
            failed,
            label: `${outcome} ×${target.max}`,
            title: `${step.id}: ${outcome} → ${routeText(target)}`,
          },
        ],
  )
}

/** Each arc with its lane, counted out from the line: one past every shorter arc it overlaps, so
 * neither two arcs nor their labels meet. */
function laned(arcs: Arc[]): (Arc & { lane: number })[] {
  const span = (arc: Arc) => arc.from - arc.to
  const placed: (Arc & { lane: number })[] = []
  for (const arc of arcs.toSorted((one, other) => span(one) - span(other))) {
    const under = placed.filter((each) => each.to < arc.from && arc.to < each.from)
    placed.push({ ...arc, lane: 1 + Math.max(0, ...under.map((each) => each.lane)) })
  }
  return placed
}

/** A curve from `x1` to `x2` that leaves the line at `y` and reaches `reach` from it at its
 * middle: up for a negative reach. */
function curve(x1: number, x2: number, y: number, reach: number): string {
  // A cubic curve's middle reaches three quarters of the way to its control points.
  const control = y + (reach * 4) / 3
  return `M${x1},${y} C${x1},${control} ${x2},${control} ${x2},${y}`
}

function StepCard({
  step,
  open,
  onToggle,
  children,
}: {
  step: FactoryStep
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <Card size="sm" variant={step.off ? "off" : "default"}>
      <CardHeader>
        <CardTitle>
          <h3 className={cn("font-mono", step.off && "text-muted-foreground line-through")}>
            <button type="button" aria-expanded={open} onClick={onToggle}>
              {step.id}
            </button>
          </h3>
        </CardTitle>
        <CardDescription>
          {[step.kind, step.engine, step.model].filter(Boolean).join(" · ")}
        </CardDescription>
        {step.off && (
          <CardAction>
            <Badge variant="outline">Off</Badge>
          </CardAction>
        )}
      </CardHeader>
      {open ? (
        <CardContent>{children}</CardContent>
      ) : (
        step.description != null && (
          <CardContent>
            <p>{step.description}</p>
          </CardContent>
        )
      )}
    </Card>
  )
}
