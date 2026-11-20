import { useId } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

type SendBack = { back: string; max: number }
type Retry = { retry: number }

/** The parts of a step of a `factory.yaml` the map and the cards show. The hub checked the rest.
 * A part the file declares as null is absent. */
export interface FactoryStep {
  id: string
  kind: string
  description?: string | null
  engine?: string
  model?: string | null
  resume?: string | null
  outcomes?: Record<string, string | SendBack>
  failed?: SendBack | Retry | null
}

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
 * as dotted arcs under it, above a card for each step. */
export function FactorySteps({ steps }: { steps: FactoryStep[] }) {
  const prefix = useId()
  const cardId = (id: string) => `${prefix}${id}`
  return (
    <div className="flex flex-col gap-4">
      <FactoryMap
        steps={steps}
        cardId={cardId}
        onPick={(id) =>
          document
            .getElementById(cardId(id))
            ?.scrollIntoView({ behavior: "smooth", block: "start" })
        }
      />
      <ol aria-label="Steps" className="flex flex-col gap-3">
        {steps.map((step) => (
          <li key={step.id} id={cardId(step.id)} className="scroll-mt-24">
            <StepCard step={step} />
          </li>
        ))}
      </ol>
    </div>
  )
}

function FactoryMap({
  steps,
  cardId,
  onPick,
}: {
  steps: FactoryStep[]
  cardId: (id: string) => string
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
              className="fill-background stroke-foreground group-hover:stroke-primary"
            />
            <text
              x={x(place)}
              y={below + 16}
              textAnchor="middle"
              fontSize={11}
              className="fill-foreground font-mono"
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
  const routes: { outcome: string; target: string | SendBack | Retry; failed: boolean }[] =
    Object.entries(step.outcomes ?? {}).map(([outcome, target]) => ({
      outcome,
      target,
      failed: false,
    }))
  if (step.failed != null) routes.push({ outcome: "failed", target: step.failed, failed: true })
  return routes.flatMap(({ outcome, target, failed }) =>
    typeof target === "object" && "back" in target
      ? [
          {
            from,
            to: at(target.back),
            failed,
            label: `${outcome} ×${target.max}`,
            title: `${step.id}: ${outcome} → back to ${target.back}, up to ${target.max} ${target.max === 1 ? "time" : "times"}`,
          },
        ]
      : [],
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

function StepCard({ step }: { step: FactoryStep }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h3 className="font-mono">{step.id}</h3>
        </CardTitle>
        <CardDescription>
          {[step.kind, step.engine, step.model].filter(Boolean).join(" · ")}
        </CardDescription>
      </CardHeader>
      {step.description != null && (
        <CardContent>
          <p>{step.description}</p>
        </CardContent>
      )}
    </Card>
  )
}
