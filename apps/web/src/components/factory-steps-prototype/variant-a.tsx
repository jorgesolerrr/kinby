// PROTOTYPE (#1450), throwaway. Variant A, "Rail and page": the steps as a vertical list of cards,
// each back route drawn as a rail down the left side. The step editor is its own page, the form on
// the left, the read-only values and the routes on the right, the text editors full width below.
import { cn } from "cn"
import { ArrowLeftIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { useLayoutEffect, useRef, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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
  To,
} from "./shared"
import { isDirty, saveAndRetry, stepTexts, targetText, useNewStep } from "./logic"
import {
  addStep,
  backOf,
  blockers,
  deleteStep,
  type Route,
  routesOf,
  saveStep,
  setOff,
  useFactory,
} from "./store"

const LANE = 14

export function VariantA() {
  const [kind, id] = useAt()
  return (
    <Shell>
      {kind === "step" ? (
        <Editor key={id} id={id} />
      ) : kind === "add" ? (
        <Add after={id} />
      ) : kind === "run" ? (
        <RunPage editAt={(step) => `step:${step}`} />
      ) : (
        <FactorySteps />
      )}
    </Shell>
  )
}

function FactorySteps() {
  const { steps } = useFactory()
  const routes = routesOf(steps).filter((route) => route.kind !== "resume")
  const list = useRef<HTMLOListElement>(null)
  const [ys, setYs] = useState<Record<string, number>>({})
  const [height, setHeight] = useState(0)
  useLayoutEffect(() => {
    const measure = () => {
      const top = list.current?.getBoundingClientRect().top ?? 0
      const next: Record<string, number> = {}
      list.current?.querySelectorAll<HTMLElement>("[data-step]").forEach((card) => {
        const box = card.getBoundingClientRect()
        next[card.dataset.step!] = box.top - top + 28
      })
      setYs(next)
      setHeight(list.current?.offsetHeight ?? 0)
    }
    measure()
    const observer = new ResizeObserver(measure)
    if (list.current) observer.observe(list.current)
    return () => observer.disconnect()
  }, [steps])
  // Shorter rails take the inner lanes, so they don't cross the longer ones.
  const span = (route: Route) => Math.abs((ys[route.from] ?? 0) - (ys[route.to] ?? 0))
  const lanes = [...routes].sort((a, b) => span(a) - span(b))
  const gutter = lanes.length * LANE + 16
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6 md:px-8">
      <div>
        <p className="text-sm text-muted-foreground">
          <To at="factory">Factories</To>
        </p>
        <h1 className="font-heading text-2xl font-semibold">
          <span className="font-mono">{FACTORY}</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          Turns issues labeled ready-for-agent into agent pull requests. A change to a step reaches
          every line on the factory.
        </p>
      </div>
      <Saved />
      <div className="flex">
        <svg
          className="pointer-events-none shrink-0 overflow-visible"
          width={gutter}
          height={height}
          aria-hidden
        >
          <defs>
            <marker
              id="a-arrow"
              viewBox="0 0 6 6"
              refX="5"
              refY="3"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M0,0 L6,3 L0,6 z" className="fill-muted-foreground" />
            </marker>
          </defs>
          {lanes.map((route, lane) => {
            const y1 = ys[route.from]
            const y2 = ys[route.to]
            if (y1 === undefined || y2 === undefined) return null
            const x = gutter - 12 - (lane + 1) * LANE
            const off = steps.find((step) => step.id === route.from)?.off
            return (
              <path
                key={`${route.from}-${route.label}`}
                d={`M${gutter - 4},${y1 + 6} H${x + 6} Q${x},${y1 + 6} ${x},${y1} V${y2 + 6} Q${x},${y2} ${x + 6},${y2} H${gutter - 4}`}
                fill="none"
                markerEnd="url(#a-arrow)"
                strokeWidth={1.5}
                strokeDasharray={off ? "3 3" : undefined}
                className={cn(
                  route.kind === "failed" ? "stroke-destructive/70" : "stroke-muted-foreground/70",
                  off && "opacity-50",
                )}
              />
            )
          })}
        </svg>
        <ol ref={list} className="flex min-w-0 flex-1 flex-col">
          {steps.map((step, index) => (
            <li key={step.id} className="flex flex-col">
              <StepCard step={step} />
              <AddBetween after={step.id} last={index === steps.length - 1} />
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

function StepCard({ step }: { step: Step }) {
  const back = [
    ...(step.failed ? [{ label: "failed", ...step.failed }] : []),
    ...Object.entries(step.outcomes ?? {}).flatMap(([label, target]) => {
      const found = backOf(target)
      return found ? [{ label, ...found }] : []
    }),
  ]
  const ends = Object.entries(step.outcomes ?? {}).filter(
    ([, target]) => typeof target === "string",
  )
  return (
    <a
      href={`?variant=A&at=step:${step.id}`}
      data-step={step.id}
      onClick={(event) => {
        event.preventDefault()
        go(`step:${step.id}`)
      }}
      className={cn(
        "flex flex-col gap-2 rounded-xl border bg-card p-3 hover:bg-muted/50",
        step.off && "border-dashed bg-transparent text-muted-foreground",
        step.added && "ring-2 ring-success/40",
      )}
    >
      <div className="flex items-center gap-2">
        <KindIcon kind={step.kind} />
        <span className={cn("font-mono font-medium", step.off && "line-through")}>{step.id}</span>
        <span className="text-xs text-muted-foreground">
          {step.kind}
          {step.engine !== undefined && ` · ${step.engine} · ${step.model}`}
        </span>
        {step.off && <Badge variant="outline">Off</Badge>}
        {step.resume !== undefined && (
          <Badge variant="secondary" className="ml-auto">
            resumes {step.resume}
          </Badge>
        )}
      </div>
      <p className="line-clamp-1 text-sm text-muted-foreground">{step.description}</p>
      {(back.length > 0 || ends.length > 0) && (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {back.map(({ label, back: to, max }) => (
            <span
              key={label}
              className={cn(
                "rounded-sm border px-1.5 py-0.5",
                label === "failed" && "border-destructive/40 text-destructive",
              )}
            >
              {label} ↩ <span className="font-mono">{to}</span>{" "}
              <span className="text-muted-foreground">up to {max}×</span>
            </span>
          ))}
          {ends.map(([label, target]) => (
            <span key={label} className="rounded-sm bg-muted px-1.5 py-0.5">
              {label} → {targetText(target)}
            </span>
          ))}
        </div>
      )}
    </a>
  )
}

function AddBetween({ after, last }: { after: string; last: boolean }) {
  return (
    <div className={cn("group flex h-6 items-center", last && "h-10")}>
      <button
        type="button"
        onClick={() => go(`add:${after}`)}
        className="flex items-center gap-1 rounded-md px-2 text-xs text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
      >
        <PlusIcon className="size-3" />
        Add a step after {after}
      </button>
    </div>
  )
}

function Editor({ id }: { id: string }) {
  const { steps } = useFactory()
  const step = steps.find((each) => each.id === id)
  const [draft, setDraft] = useState(step)
  const [refused, setRefused] = useState(false)
  if (step === undefined || draft === undefined) return <p className="p-8">No step {id}.</p>
  const texts = stepTexts(draft)
  const dirty = isDirty(draft, step)
  const into = routesOf(steps).filter((route) => route.to === id && route.kind !== "resume")
  const toggle = (on: boolean) => {
    if (!on && blockers(steps, id).length > 0) return setRefused(true)
    setRefused(false)
    setOff(id, !on)
    setDraft({ ...draft, off: !on })
  }
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6 md:px-8">
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">
          <To at="factory">
            <ArrowLeftIcon className="inline size-3" /> {FACTORY}
          </To>{" "}
          / step {steps.indexOf(step) + 1} of {steps.length}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <KindIcon kind={step.kind} className="size-5" />
          <h1 className="font-mono text-2xl font-semibold">{step.id}</h1>
          <Badge variant="outline">{step.kind} step</Badge>
          <label className="ml-auto flex items-center gap-2 text-sm">
            <Switch checked={!step.off} onCheckedChange={toggle} />
            {step.off ? "Off" : "On"}
          </label>
          {step.off && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                deleteStep(id)
                go("factory")
              }}
            >
              <Trash2Icon data-icon="inline-start" />
              Delete
            </Button>
          )}
        </div>
        <Textarea
          aria-label="Description"
          rows={2}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          className="resize-none"
        />
      </div>
      <FromRun step={id} onSaveAndRetry={() => saveAndRetry(() => saveStep(id, draft))} />
      {refused && <Refusal id={id} onClose={() => setRefused(false)} />}
      <div className="grid gap-6 md:grid-cols-3">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Settings</CardTitle>
          </CardHeader>
          <CardContent>
            <StepFields draft={draft} setDraft={setDraft} steps={steps} columns={2} />
          </CardContent>
        </Card>
        <div className="flex flex-col gap-4">
          <Card size="sm">
            <CardHeader>
              <CardTitle>Read-only</CardTitle>
              <CardDescription>Set by the step's code.</CardDescription>
            </CardHeader>
            <CardContent>
              <ReadOnlyValues step={step} />
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <CardTitle>Routes in</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col gap-1 text-sm">
                {into.length === 0 && <span className="text-muted-foreground">None</span>}
                {into.map((route) => (
                  <span key={route.from + route.label}>
                    <To at={`step:${route.from}`} className="font-mono">
                      {route.from}
                    </To>{" "}
                    <span className="text-muted-foreground">
                      {route.label}, up to {route.max}×
                    </span>
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
      {texts.length > 0 && (
        <Tabs defaultValue={texts[0].key}>
          <TabsList variant="line">
            {texts.map((text) => (
              <TabsTrigger key={text.key} value={text.key}>
                {text.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {texts.map((text) => (
            <TabsContent key={text.key} value={text.key}>
              <CodeEditor
                file={text.file}
                language={text.language}
                value={text.value}
                rows={28}
                onChange={(value) => setDraft({ ...draft, ...text.put(value) })}
              />
            </TabsContent>
          ))}
        </Tabs>
      )}
      <div className="sticky bottom-16 flex justify-end gap-2 rounded-xl border bg-background/95 p-2 shadow-sm">
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
  )
}

function Add({ after }: { after: string }) {
  const made = useNewStep()
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6 px-4 py-6">
      <div>
        <p className="text-sm text-muted-foreground">
          <To at="factory">{FACTORY}</To>
        </p>
        <h1 className="font-heading text-2xl font-semibold">
          Add a step after <span className="font-mono">{after}</span>
        </h1>
      </div>
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
          Add and open its editor
        </Button>
      </div>
    </div>
  )
}
