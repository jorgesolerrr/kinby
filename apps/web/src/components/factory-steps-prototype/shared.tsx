// PROTOTYPE (#1450), throwaway. What the variants share: the switcher, a bare app shell, the step
// form's fields, the text editors, the factory check's refusal, and the run page with its failed
// attempt. Each variant places these its own way.
import { cn } from "cn"
import {
  BotIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CodeIcon,
  FactoryIcon,
  LibraryIcon,
  LockIcon,
  RotateCcwIcon,
  TerminalIcon,
  TimerIcon,
} from "lucide-react"
import type * as React from "react"
import { useEffect } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Textarea } from "@/components/ui/textarea"
import { TooltipProvider } from "@/components/ui/tooltip"

import {
  CHECKS,
  COMMANDS,
  EFFORTS,
  ENGINES,
  MODELS,
  RUN,
  type Step,
  type StepKind,
  type Target,
} from "./data"
import { go, param, setParams, useParam } from "./nav"
import { blockers, clearSaved, retryRun, useFactory } from "./store"

const KIND_ICONS: Record<StepKind, typeof BotIcon> = {
  agent: BotIcon,
  command: TerminalIcon,
  code: CodeIcon,
  wait: TimerIcon,
}

export function KindIcon({ kind, className }: { kind: StepKind; className?: string }) {
  const Icon = KIND_ICONS[kind]
  return <Icon className={cn("size-4 shrink-0", className)} />
}

/** A link that opens a screen of the prototype. */
export function To({
  at,
  className,
  children,
}: {
  at: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <a
      href={`?variant=${param("variant") ?? "A"}&at=${at}`}
      className={cn("underline-offset-4 hover:underline", className)}
      onClick={(event) => {
        event.preventDefault()
        go(at)
      }}
    >
      {children}
    </a>
  )
}

/** The floating bar that cycles variants. Not part of any design. */
export function PrototypeSwitcher({ variants }: { variants: { key: string; name: string }[] }) {
  const current = useParam("variant") ?? variants[0].key
  const index = Math.max(
    0,
    variants.findIndex((v) => v.key === current),
  )
  const cycle = (by: number) =>
    setParams({ variant: variants[(index + by + variants.length) % variants.length].key }, false)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.closest("input, textarea, [contenteditable], [role=combobox], [role=listbox]"))
        return
      if (event.key === "ArrowLeft") cycle(-1)
      if (event.key === "ArrowRight") cycle(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })
  if (import.meta.env.PROD) return null
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full bg-foreground p-1 text-background shadow-xl">
      <button
        type="button"
        aria-label="Previous variant"
        className="rounded-full p-1.5 hover:bg-background/20"
        onClick={() => cycle(-1)}
      >
        <ChevronLeftIcon className="size-4" />
      </button>
      <span className="px-2 font-mono text-xs">
        {variants[index].key} ({variants[index].name})
      </span>
      <button
        type="button"
        aria-label="Next variant"
        className="rounded-full p-1.5 hover:bg-background/20"
        onClick={() => cycle(1)}
      >
        <ChevronRightIcon className="size-4" />
      </button>
    </div>
  )
}

/** The app's shell with Factories open, so the page sits at its real width. */
export function Shell({ fixed = false, children }: { fixed?: boolean; children: React.ReactNode }) {
  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar collapsible="offcanvas">
          <SidebarHeader>
            <div className="flex items-center gap-2 px-2 py-1">
              <KinbyMark className="size-5" />
              <span className="font-heading font-semibold">kinby</span>
            </div>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton>
                    <LibraryIcon />
                    Library
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton isActive onClick={() => go("factory", null)}>
                    <FactoryIcon />
                    Factories
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton onClick={() => go("run", null)}>
                    <span className="size-4 rounded-full bg-ember" />
                    Run #1412
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>
        <SidebarInset className={cn("min-w-0", fixed && "h-svh")}>
          <header className="flex h-12 shrink-0 items-center gap-2 px-3">
            <SidebarTrigger />
          </header>
          <div className={cn("flex min-h-0 flex-1 flex-col", !fixed && "pb-20")}>{children}</div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}

/** The last save, said once under the page's title. */
export function Saved() {
  const { saved } = useFactory()
  useEffect(() => {
    if (saved === undefined) return
    const timer = window.setTimeout(clearSaved, 4000)
    return () => window.clearTimeout(timer)
  }, [saved])
  if (saved === undefined) return null
  return (
    <output className="block rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
      {saved}
    </output>
  )
}

/** A small select over string options. */
export function Pick({
  id,
  value,
  options,
  onChange,
  className,
}: {
  id?: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  className?: string
}) {
  return (
    <Select items={options} value={value} onValueChange={(next) => onChange(String(next))}>
      <SelectTrigger id={id} className={cn("w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

const opts = (values: string[]) => values.map((value) => ({ value, label: value }))

/** One target: next, done, stop, or back to an earlier step with its max. */
function TargetField({
  value,
  steps,
  self,
  onChange,
  allowEnds = true,
}: {
  value: Target | undefined
  steps: Step[]
  self: string
  onChange: (value: Target | undefined) => void
  allowEnds?: boolean
}) {
  const earlier = steps.slice(0, steps.findIndex((step) => step.id === self) + 1)
  const options = [
    ...(allowEnds
      ? opts(["next", "done", "stop"])
      : [{ value: "none", label: "Needs a human (no route)" }]),
    ...earlier.map((step) => ({ value: `back:${step.id}`, label: `Back to ${step.id}` })),
  ]
  const current =
    value === undefined ? "none" : typeof value === "string" ? value : `back:${value.back}`
  return (
    <div className="flex gap-2">
      <Pick
        value={current}
        options={options}
        onChange={(next) => {
          if (next === "none") onChange(undefined)
          else if (next.startsWith("back:"))
            onChange({ back: next.slice(5), max: typeof value === "object" ? value.max : 2 })
          else onChange(next as Target)
        }}
      />
      {typeof value === "object" && (
        <div className="flex shrink-0 items-center gap-1 text-sm text-muted-foreground">
          up to
          <Input
            type="number"
            min={1}
            aria-label="max"
            className="w-16"
            value={value.max}
            onChange={(event) => onChange({ ...value, max: Number(event.target.value) })}
          />
          times
        </div>
      )}
    </div>
  )
}

/**
 * The step's form fields for its kind. `columns` lays them out two abreast where the variant has
 * the width.
 */
export function StepFields({
  draft,
  setDraft,
  steps,
  columns = 1,
}: {
  draft: Step
  setDraft: (step: Step) => void
  steps: Step[]
  columns?: 1 | 2
}) {
  const put = (patch: Partial<Step>) => setDraft({ ...draft, ...patch })
  const agents = steps.filter((step) => step.kind === "agent" && step.id !== draft.id)
  return (
    <FieldGroup className={cn(columns === 2 && "grid md:grid-cols-2")}>
      {draft.kind === "agent" && (
        <>
          <Field>
            <FieldLabel>Engine</FieldLabel>
            <Pick
              value={draft.engine ?? "claude"}
              options={opts(ENGINES)}
              onChange={(engine) => put({ engine, model: MODELS[engine][0] })}
            />
          </Field>
          <Field>
            <FieldLabel>Model</FieldLabel>
            <Pick
              value={draft.model ?? ""}
              options={opts(MODELS[draft.engine ?? "claude"])}
              onChange={(model) => put({ model })}
            />
          </Field>
          <Field>
            <FieldLabel>Effort</FieldLabel>
            <Pick
              value={draft.effort ?? "high"}
              options={opts(EFFORTS)}
              onChange={(effort) => put({ effort })}
            />
          </Field>
          <Field>
            <FieldLabel>Timeout</FieldLabel>
            <Input value={draft.timeout ?? ""} onChange={(e) => put({ timeout: e.target.value })} />
          </Field>
          <Field>
            <FieldLabel>Resume</FieldLabel>
            <Pick
              value={draft.resume ?? "none"}
              options={[
                { value: "none", label: "A new session" },
                ...agents.map((step) => ({
                  value: step.id,
                  label: `${step.id}'s session`,
                })),
              ]}
              onChange={(resume) => put({ resume: resume === "none" ? undefined : resume })}
            />
          </Field>
        </>
      )}
      {draft.kind === "wait" && (
        <Field>
          <FieldLabel>Quiet</FieldLabel>
          <Input value={draft.quiet ?? ""} onChange={(e) => put({ quiet: e.target.value })} />
          <FieldDescription>
            With no signal for this long, the wait moves on by itself.
          </FieldDescription>
        </Field>
      )}
      <Field>
        <FieldLabel>Retry</FieldLabel>
        <Input
          type="number"
          min={0}
          value={draft.retry ?? 1}
          onChange={(e) => put({ retry: Number(e.target.value) })}
        />
        <FieldDescription>Attempts after the first, before a failure counts.</FieldDescription>
      </Field>
      {draft.kind !== "wait" && (
        <Field>
          <FieldLabel>When it fails</FieldLabel>
          <TargetField
            value={draft.failed}
            steps={steps}
            self={draft.id}
            allowEnds={false}
            onChange={(failed) => put({ failed: failed as Step["failed"] })}
          />
        </Field>
      )}
      {Object.entries(draft.outcomes ?? {}).map(([outcome, target]) => (
        <Field key={outcome}>
          <FieldLabel>
            Outcome <span className="font-mono">{outcome}</span>
          </FieldLabel>
          <TargetField
            value={target}
            steps={steps}
            self={draft.id}
            onChange={(next) => put({ outcomes: { ...draft.outcomes, [outcome]: next ?? "next" } })}
          />
        </Field>
      ))}
    </FieldGroup>
  )
}

/** `requires`, `results` and a command step's run list, which the editor shows but does not change. */
export function ReadOnlyValues({ step, className }: { step: Step; className?: string }) {
  const rows: [string, React.ReactNode][] = []
  if (step.run !== undefined)
    rows.push(["run", step.run.map((each) => `commands: ${each}`).join(", ")])
  rows.push(["requires", step.requires?.join(", ") || "nothing"])
  rows.push([
    "results",
    Object.entries(step.results ?? {})
      .map(([name, type]) => `${name}: ${type}`)
      .join(", ") || "nothing",
  ])
  return (
    <dl className={cn("flex flex-col gap-1 text-sm", className)}>
      {rows.map(([name, value]) => (
        <div key={name} className="flex gap-3">
          <dt className="flex w-20 shrink-0 items-center gap-1 text-muted-foreground">
            <LockIcon className="size-3" />
            {name}
          </dt>
          <dd className="font-mono text-xs leading-5">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A text editor stand-in: a mono textarea with the file's name over it. */
export function CodeEditor({
  file,
  language,
  value,
  onChange,
  rows = 18,
  className,
}: {
  file: string
  language: "markdown" | "python" | "yaml"
  value: string
  onChange: (value: string) => void
  rows?: number
  className?: string
}) {
  return (
    <div className={cn("flex min-h-0 flex-col overflow-hidden rounded-lg border", className)}>
      <div className="flex items-center justify-between border-b bg-muted/50 px-3 py-1.5 text-xs">
        <span className="font-mono">{file}</span>
        <span className="text-muted-foreground">{language}</span>
      </div>
      {/* A plain textarea stands in for a real code editor. */}
      <textarea
        value={value}
        rows={rows}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-0 flex-1 resize-none bg-transparent px-3 py-2 font-mono text-xs leading-5 outline-none"
      />
    </div>
  )
}

/** The steps that turning `id` off would break, as the factory check says it. */
export function Refusal({ id, onClose }: { id: string; onClose?: () => void }) {
  const { steps } = useFactory()
  const found = blockers(steps, id)
  return (
    <Alert variant="destructive" layout="stack">
      <AlertTitle>The factory check keeps {id} on</AlertTitle>
      <AlertDescription>
        <ul className="list-disc pl-4">
          {found.map(({ step, why }) => (
            <li key={step + why}>
              <To at={`step:${step}`} className="font-mono font-medium">
                {step}
              </To>{" "}
              {why}
            </li>
          ))}
        </ul>
        Change or turn off {found.length === 1 ? "that step" : "those steps"} first.
      </AlertDescription>
      {onClose !== undefined && (
        <AlertAction>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </AlertAction>
      )}
    </Alert>
  )
}

/** The first fields of a new step: kind, id, description, and for an agent its check. */
export function NewStepFields({
  kind,
  setKind,
  id,
  setId,
  description,
  setDescription,
  check,
  setCheck,
  run,
  setRun,
}: {
  kind: StepKind
  setKind: (kind: StepKind) => void
  id: string
  setId: (id: string) => void
  description: string
  setDescription: (description: string) => void
  check: string
  setCheck: (check: string) => void
  run: string
  setRun: (run: string) => void
}) {
  return (
    <FieldGroup>
      <Field>
        <FieldLabel>Kind</FieldLabel>
        <div className="grid grid-cols-3 gap-2">
          {(["agent", "command", "wait"] as const).map((each) => (
            <Button
              key={each}
              variant={kind === each ? "secondary" : "outline"}
              aria-pressed={kind === each}
              onClick={() => setKind(each)}
            >
              <KindIcon kind={each} />
              {each}
            </Button>
          ))}
        </div>
        <FieldDescription>Code steps come from the factory's files or from chat.</FieldDescription>
      </Field>
      <Field>
        <FieldLabel>Id</FieldLabel>
        <Input
          value={id}
          placeholder="docs"
          variant="mono"
          onChange={(e) => setId(e.target.value)}
        />
      </Field>
      <Field>
        <FieldLabel>Description</FieldLabel>
        <Textarea
          value={description}
          rows={2}
          placeholder="What the step does, shown as its editor's header."
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      {kind === "agent" && (
        <Field>
          <FieldLabel>Check</FieldLabel>
          <Pick
            value={check}
            options={[{ value: "none", label: "No check" }, ...opts(CHECKS)]}
            onChange={setCheck}
          />
        </Field>
      )}
      {kind === "command" && (
        <Field>
          <FieldLabel>Runs</FieldLabel>
          <Pick
            value={run}
            options={COMMANDS.map((each) => ({
              value: each,
              label: `The workbench's ${each} list`,
            }))}
            onChange={setRun}
          />
        </Field>
      )}
    </FieldGroup>
  )
}

/** Shown on the editor when the user came from a run: save, then send the run back to a retry. */
export function FromRun({ step, onSaveAndRetry }: { step: string; onSaveAndRetry: () => void }) {
  const from = useParam("from")
  if (from === null) return null
  return (
    <Alert layout="stack">
      <RotateCcwIcon />
      <AlertTitle>From run #1412</AlertTitle>
      <AlertDescription>
        Its {step} attempt {RUN.attempts.at(-1)?.ending}. The change reaches every line on the
        factory; the run picks it up when it retries.
      </AlertDescription>
      <AlertAction>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => go("run", null)}>
            Back to the run
          </Button>
          <Button size="sm" onClick={onSaveAndRetry}>
            Save and retry
          </Button>
        </div>
      </AlertAction>
    </Alert>
  )
}

const ENDING_BADGES = {
  clean: "outline",
  failed: "destructive",
  "timed-out": "destructive",
} as const

/**
 * The run's page, as the app draws it today but trimmed: a row per step with its attempts. The
 * failed attempt has Edit this step, which opens the variant's editor at `editAt`.
 */
export function RunPage({ editAt }: { editAt: (step: string) => string }) {
  const { steps, retried } = useFactory()
  const failed = RUN.attempts.at(-1)!
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-6 md:px-8">
      <div className="flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">
          <To at="factory">Factories</To> / {RUN.line}
        </p>
        <h1 className="font-heading text-2xl font-semibold">{RUN.item}</h1>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          {retried ? (
            <Badge variant="secondary">Running</Badge>
          ) : (
            <Badge variant="attention">Needs you</Badge>
          )}
          started today at 09:02
        </p>
      </div>
      {!retried && (
        <Alert layout="stack">
          <AlertTitle>implement failed twice</AlertTitle>
          <AlertDescription>{failed.summary}</AlertDescription>
          <AlertAction>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={retryRun}>
                Retry
              </Button>
              <Button size="sm" onClick={() => go(editAt(failed.step), RUN.id)}>
                Edit this step
              </Button>
            </div>
          </AlertAction>
        </Alert>
      )}
      <ol className="flex flex-col gap-2">
        {steps
          .filter((step) => !step.off)
          .map((step) => {
            const attempts = RUN.attempts.filter((attempt) => attempt.step === step.id)
            const open = step.id === failed.step
            return (
              <li key={step.id} className="rounded-lg border">
                <div className="flex items-center gap-3 px-3 py-2">
                  <KindIcon kind={step.kind} className="text-muted-foreground" />
                  <span className="font-mono text-sm font-medium">{step.id}</span>
                  <span className="text-xs text-muted-foreground">
                    {step.kind}
                    {step.engine !== undefined && ` · ${step.engine}`}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {attempts.length === 0
                      ? "Not reached"
                      : `${attempts.length + (open && retried ? 1 : 0)} attempts`}
                  </span>
                </div>
                {open && (
                  <ul className="flex flex-col divide-y border-t bg-muted/40 text-sm">
                    {attempts.map((attempt) => (
                      <li key={attempt.n} className="flex flex-wrap items-center gap-3 px-3 py-2">
                        <span className="text-muted-foreground">#{attempt.n}</span>
                        <Badge variant={ENDING_BADGES[attempt.ending]}>{attempt.ending}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {attempt.started} · {attempt.took} · {attempt.cost}
                        </span>
                        <span className="w-full text-xs">{attempt.summary}</span>
                        {attempt === failed && !retried && (
                          <Button
                            size="xs"
                            variant="outline"
                            onClick={() => go(editAt(step.id), RUN.id)}
                          >
                            Edit this step
                          </Button>
                        )}
                      </li>
                    ))}
                    {retried && (
                      <li className="flex items-center gap-3 px-3 py-2">
                        <span className="text-muted-foreground">#3</span>
                        <Badge variant="secondary">running</Badge>
                        <span className="text-xs text-muted-foreground">
                          on the edited step · started just now
                        </span>
                      </li>
                    )}
                  </ul>
                )}
              </li>
            )
          })}
      </ol>
    </div>
  )
}
