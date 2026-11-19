// PROTOTYPE (#1450), throwaway. Variant C, "Map and cards": a subway map of the factory across the
// top, the steps in order on one line with each back route an arc over it, labelled with its max,
// and each resumed session a dotted arc under it. Below, the steps as cards; a card opens in place
// into its editor, the text editors side by side across the card's full width. A new step is
// added in place too, as a card between two others.
import { cn } from "cn"
import { ChevronDownIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

import { FACTORY, type Step } from "./data"
import { go, useAt } from "./nav"
import {
  CodeEditor,
  FromRun,
  KindIcon,
  NewStepFields,
  ReadOnlyValues,
  Refusal,
  RunPage,
  Saved,
  Shell,
  StepFields,
} from "./shared"
import { isDirty, saveAndRetry, stepTexts, targetText, useNewStep } from "./logic"
import { addStep, blockers, deleteStep, routesOf, saveStep, setOff, useFactory } from "./store"

const GAP = 84
const LINE_Y = 130

export function VariantC() {
  const [kind] = useAt()
  return <Shell>{kind === "run" ? <RunPage editAt={(step) => `step:${step}`} /> : <Page />}</Shell>
}

function Page() {
  const [kind, id] = useAt()
  const [refused, setRefused] = useState<string>()
  const { steps } = useFactory()
  const blocking = refused === undefined ? [] : blockers(steps, refused).map(({ step }) => step)
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-6 md:px-8">
      <div>
        <p className="text-sm text-muted-foreground">Factories</p>
        <h1 className="font-mono text-2xl font-semibold">{FACTORY}</h1>
      </div>
      <SubwayMap selected={kind === "step" ? id : undefined} blocking={blocking} />
      <Saved />
      <ol className="flex flex-col">
        {steps.map((step) => (
          <li key={step.id} className="flex flex-col">
            <StepCard
              step={step}
              open={kind === "step" && id === step.id}
              refused={refused === step.id}
              setRefused={setRefused}
              blocking={blocking.includes(step.id)}
            />
            {kind === "add" && id === step.id ? (
              <AddCard after={step.id} />
            ) : (
              <AddHere after={step.id} />
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

function SubwayMap({ selected, blocking }: { selected?: string; blocking: string[] }) {
  const { steps } = useFactory()
  const routes = routesOf(steps)
  const x = (id: string) => 40 + steps.findIndex((step) => step.id === id) * GAP
  const width = 40 * 2 + (steps.length - 1) * GAP
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <svg width={width} height={LINE_Y + 80} className="block" aria-label="The factory's steps">
        <line
          x1={40}
          x2={width - 40}
          y1={LINE_Y}
          y2={LINE_Y}
          strokeWidth={3}
          className="stroke-foreground/70"
        />
        {routes.map((route) => {
          const x1 = x(route.from)
          const x2 = x(route.to)
          const off = steps.find((step) => step.id === route.from)?.off
          if (route.kind === "resume") {
            const h = 14 + Math.abs(x1 - x2) / 10
            return (
              <path
                key={`${route.from}-resume`}
                d={`M${x1},${LINE_Y + 14} C${x1},${LINE_Y + 14 + h} ${x2},${LINE_Y + 14 + h} ${x2},${LINE_Y + 14}`}
                fill="none"
                strokeDasharray="2 3"
                strokeWidth={1.5}
                className="stroke-muted-foreground"
              />
            )
          }
          // Routes between the same two steps stack, each arc a little higher than the last.
          const twin = routes
            .filter(
              (each) => each.kind !== "resume" && each.from === route.from && each.to === route.to,
            )
            .indexOf(route)
          const h = 22 + Math.abs(x1 - x2) / 9 + twin * 20
          const mid = (x1 + x2) / 2
          return (
            <g key={`${route.from}-${route.label}`} className={cn(off && "opacity-40")}>
              <path
                d={`M${x1},${LINE_Y - 14} C${x1},${LINE_Y - 14 - h} ${x2},${LINE_Y - 14 - h} ${x2},${LINE_Y - 14}`}
                fill="none"
                strokeWidth={1.5}
                strokeDasharray={off ? "4 3" : undefined}
                className={route.kind === "failed" ? "stroke-destructive" : "stroke-primary"}
              />
              <text
                x={mid}
                y={LINE_Y - 14 - h * 0.75 - 3}
                textAnchor="middle"
                fontSize={10}
                className={cn(route.kind === "failed" ? "fill-destructive" : "fill-primary")}
              >
                {route.label} ×{route.max}
              </text>
            </g>
          )
        })}
        {steps.map((step) => {
          const cx = x(step.id)
          return (
            <g key={step.id} className="cursor-pointer" onClick={() => go(`step:${step.id}`)}>
              <circle
                cx={cx}
                cy={LINE_Y}
                r={selected === step.id ? 11 : 9}
                strokeWidth={3}
                strokeDasharray={step.off ? "3 3" : undefined}
                className={cn(
                  step.off
                    ? "fill-background stroke-muted-foreground"
                    : "fill-background stroke-foreground",
                  selected === step.id && "fill-primary stroke-primary",
                  blocking.includes(step.id) && "animate-pulse fill-destructive stroke-destructive",
                )}
              />
              <text
                x={cx}
                y={LINE_Y + 50}
                textAnchor="middle"
                fontSize={11}
                className={cn(
                  "font-mono",
                  step.off ? "fill-muted-foreground" : "fill-foreground",
                  selected === step.id && "font-semibold",
                )}
              >
                {step.id}
              </text>
              <text
                x={cx}
                y={LINE_Y + 64}
                textAnchor="middle"
                fontSize={9}
                className="fill-muted-foreground"
              >
                {step.off
                  ? "off"
                  : step.kind === "agent"
                    ? step.model?.replace("claude-", "")
                    : step.kind}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function StepCard({
  step,
  open,
  refused,
  setRefused,
  blocking,
}: {
  step: Step
  open: boolean
  refused: boolean
  setRefused: (id: string | undefined) => void
  blocking: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [open])
  return (
    <div
      ref={ref}
      className={cn(
        "scroll-mt-4 rounded-xl border bg-card",
        step.off && "border-dashed bg-transparent",
        open && "ring-2 ring-primary/30",
        blocking && "border-destructive",
      )}
    >
      <button
        type="button"
        onClick={() => go(open ? "factory" : `step:${step.id}`)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <KindIcon kind={step.kind} className={cn(step.off && "text-muted-foreground")} />
        <span
          className={cn("font-mono font-medium", step.off && "text-muted-foreground line-through")}
        >
          {step.id}
        </span>
        {step.off && <Badge variant="outline">Off</Badge>}
        {step.added && <Badge variant="success">New</Badge>}
        <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
          {open ? "" : step.description}
        </span>
        <span className="hidden shrink-0 text-xs text-muted-foreground md:inline">
          {step.kind === "agent" ? `${step.engine} · ${step.model}` : step.kind}
        </span>
        <ChevronDownIcon className={cn("size-4 shrink-0 transition", open && "rotate-180")} />
      </button>
      {open && <InCardEditor step={step} refused={refused} setRefused={setRefused} />}
    </div>
  )
}

function InCardEditor({
  step,
  refused,
  setRefused,
}: {
  step: Step
  refused: boolean
  setRefused: (id: string | undefined) => void
}) {
  const { steps } = useFactory()
  const [draft, setDraft] = useState(step)
  const texts = stepTexts(draft)
  const dirty = isDirty(draft, step)
  const id = step.id
  return (
    <div className="flex flex-col gap-4 border-t px-4 py-4">
      <div className="flex items-start gap-4">
        <Textarea
          aria-label="Description"
          rows={2}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          className="flex-1 resize-none"
        />
        <label className="flex shrink-0 items-center gap-2 pt-2 text-sm">
          <Switch
            checked={!step.off}
            onCheckedChange={(on) => {
              if (!on && blockers(steps, id).length > 0) return setRefused(id)
              setRefused(undefined)
              setOff(id, !on)
              setDraft({ ...draft, off: !on })
            }}
          />
          {step.off ? "Off" : "On"}
        </label>
      </div>
      <FromRun step={id} onSaveAndRetry={() => saveAndRetry(() => saveStep(id, draft))} />
      {refused && <Refusal id={id} onClose={() => setRefused(undefined)} />}
      <StepFields draft={draft} setDraft={setDraft} steps={steps} columns={2} />
      {texts.length > 0 && (
        <div className={cn("grid gap-3", texts.length > 1 && "lg:grid-cols-2")}>
          {texts.map((text) => (
            <div key={text.key} className="flex flex-col gap-1">
              <span className="text-xs font-medium">{text.label}</span>
              <CodeEditor
                file={text.file}
                language={text.language}
                value={text.value}
                rows={22}
                onChange={(value) => setDraft({ ...draft, ...text.put(value) })}
              />
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-end justify-between gap-4 border-t pt-3">
        <ReadOnlyValues step={step} />
        <div className="flex gap-2">
          {step.off && (
            <Button
              variant="ghost"
              onClick={() => {
                deleteStep(id)
                go("factory")
              }}
            >
              <Trash2Icon data-icon="inline-start" />
              Delete
            </Button>
          )}
          <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(step)}>
            Discard
          </Button>
          <Button
            disabled={!dirty}
            onClick={() => {
              saveStep(id, draft)
              go("factory")
            }}
          >
            Save
          </Button>
        </div>
      </div>
      {Object.keys(step.outcomes ?? {}).length > 0 && (
        <p className="text-xs text-muted-foreground">
          Now:{" "}
          {Object.entries(step.outcomes ?? {})
            .map(([outcome, target]) => `${outcome} → ${targetText(target)}`)
            .join("; ")}
        </p>
      )}
    </div>
  )
}

function AddHere({ after }: { after: string }) {
  return (
    <div className="group relative flex h-5 items-center justify-center">
      <div className="absolute inset-x-0 top-1/2 border-t border-dashed opacity-0 group-hover:opacity-100" />
      <button
        type="button"
        aria-label={`Add a step after ${after}`}
        onClick={() => go(`add:${after}`)}
        className="relative z-10 flex size-5 items-center justify-center rounded-full border bg-background text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
      >
        <PlusIcon className="size-3" />
      </button>
    </div>
  )
}

function AddCard({ after }: { after: string }) {
  const made = useNewStep()
  return (
    <div className="my-3 flex flex-col gap-4 rounded-xl border-2 border-dashed border-primary/40 p-4">
      <h2 className="font-medium">
        New step after <span className="font-mono">{after}</span>
      </h2>
      <NewStepFields {...made.fields} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => go("factory")}>
          Cancel
        </Button>
        <Button
          disabled={!made.ready}
          onClick={() => {
            const step = made.step()
            addStep(after, step)
            go(`step:${step.id}`)
          }}
        >
          Add
        </Button>
      </div>
    </div>
  )
}
