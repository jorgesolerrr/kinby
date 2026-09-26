// PROTOTYPE, throwaway. Leaf pieces the variants share; each variant owns its own layout.
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "cn"
import { CheckIcon, CircleAlertIcon, CircleIcon, KeyRoundIcon, SendIcon } from "lucide-react"

import type { Flow, FlowState, Stage, StepState } from "./flow"
import { STAGES } from "./flow"
import { COLORS, type PackageCard, SHAPES, type SetupField, type Shape } from "./stub"

const shapeClass: Record<Shape, string> = {
  circle: "rounded-full",
  squircle: "rounded-2xl",
  square: "rounded-md",
}

export function InstanceAvatar({
  name,
  shape,
  color,
  size = "size-16 text-2xl",
}: {
  name: string
  shape: Shape
  color: string
  size?: string
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center bg-(--avatar) font-semibold text-white",
        shapeClass[shape],
        size,
      )}
      style={{ "--avatar": color } as React.CSSProperties}
    >
      {(name.trim()[0] ?? "k").toUpperCase()}
    </div>
  )
}

export function AvatarPicker({ state, patch }: { state: FlowState; patch: Flow["patch"] }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        {SHAPES.map((shape) => (
          <button
            key={shape}
            type="button"
            aria-label={shape}
            onClick={() => patch({ shape })}
            className={cn(
              "rounded-lg p-1 ring-offset-2",
              state.shape === shape && "ring-2 ring-ring",
            )}
          >
            <InstanceAvatar
              name={state.name}
              shape={shape}
              color={state.color}
              size="size-8 text-sm"
            />
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        {COLORS.map((color) => (
          <button
            key={color}
            type="button"
            aria-label={color}
            onClick={() => patch({ color })}
            className={cn(
              "size-6 rounded-full bg-(--avatar) ring-offset-2",
              state.color === color && "ring-2 ring-ring",
            )}
            style={{ "--avatar": color } as React.CSSProperties}
          />
        ))}
      </div>
    </div>
  )
}

export function FieldsForm({
  fields,
  state,
  patch,
  kinds = ["config", "secret"],
  errorField,
}: {
  fields: SetupField[]
  state: FlowState
  patch: Flow["patch"]
  kinds?: SetupField["kind"][]
  errorField?: string
}) {
  const shown = fields.filter((f) => kinds.includes(f.kind))
  const groups = [
    { kind: "config" as const, legend: "Configuration" },
    { kind: "secret" as const, legend: "Secrets" },
  ].filter((g) => shown.some((f) => f.kind === g.kind))
  return (
    <FieldGroup>
      {groups.map((group) => (
        <FieldSet key={group.kind}>
          <FieldLegend>{group.legend}</FieldLegend>
          {group.kind === "secret" && (
            <FieldDescription>
              Write-only. The hub stores them for this instance and never sends them back.
            </FieldDescription>
          )}
          {shown
            .filter((f) => f.kind === group.kind)
            .map((field) => (
              <Field key={field.name} data-invalid={errorField === field.name || undefined}>
                <FieldLabel htmlFor={field.name}>
                  {field.label}
                  {!field.required && <span className="text-muted-foreground">(optional)</span>}
                </FieldLabel>
                <Input
                  id={field.name}
                  type={field.kind === "secret" ? "password" : "text"}
                  placeholder={field.default ?? (field.kind === "secret" ? "Paste the value" : "")}
                  value={state.values[field.name] ?? ""}
                  aria-invalid={errorField === field.name || undefined}
                  onChange={(e) =>
                    patch({ values: { ...state.values, [field.name]: e.target.value } })
                  }
                />
                <FieldDescription>{field.description}</FieldDescription>
              </Field>
            ))}
        </FieldSet>
      ))}
    </FieldGroup>
  )
}

export function BehaviorPrompt({ state, patch }: { state: FlowState; patch: Flow["patch"] }) {
  return (
    <Field>
      <FieldLabel htmlFor="behavior">
        Behavior prompt <span className="text-muted-foreground">(optional)</span>
      </FieldLabel>
      <Textarea
        id="behavior"
        rows={4}
        placeholder="You are my assistant for… Keep answers short."
        value={state.values.behavior ?? ""}
        onChange={(e) => patch({ values: { ...state.values, behavior: e.target.value } })}
      />
      <FieldDescription>
        Goes to SYSTEM.md. Tools and skills are picked later, in the instance's config.
      </FieldDescription>
    </Field>
  )
}

const stepIcon: Record<StepState, React.ReactNode> = {
  pending: <CircleIcon className="text-muted-foreground" />,
  running: <Spinner />,
  succeeded: <CheckIcon />,
  failed: <CircleAlertIcon className="text-destructive" />,
}

export function OperationSteps({ steps }: { steps: FlowState["steps"] }) {
  return (
    <ol className="flex flex-col gap-2 text-sm">
      {steps.map((step) => (
        <li
          key={step.name}
          className={cn(
            "flex items-center gap-2 [&_svg]:size-4",
            step.state === "pending" && "text-muted-foreground",
            step.state === "failed" && "text-destructive",
          )}
        >
          {stepIcon[step.state]}
          <span className="capitalize">{step.name}</span>
        </li>
      ))}
    </ol>
  )
}

export function FailureAlert({ state, onFix }: { state: FlowState; onFix: () => void }) {
  if (state.error === null) return null
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>Preparation stopped at “{state.error.step}”</AlertTitle>
      <AlertDescription>
        <p>{state.error.detail}</p>
        <p>Nothing was published: no instance exists yet and the directory stays empty.</p>
        <Button size="sm" variant="outline" className="mt-2" onClick={onFix}>
          Fix the GitHub token
        </Button>
      </AlertDescription>
    </Alert>
  )
}

export function LoginList({
  card,
  flow,
  instanceId,
}: {
  card: PackageCard
  flow: Flow
  instanceId: string
}) {
  const { state } = flow
  return (
    <ul className="flex flex-col gap-3">
      {card.logins.map((login) => {
        const status = state.logins[login.id] ?? "pending"
        return (
          <li key={login.id} className="flex items-center gap-3 rounded-lg border p-3">
            <KeyRoundIcon className="size-4 text-muted-foreground" />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm font-medium">{login.label}</span>
              <span className="text-xs text-muted-foreground">
                {status === "waiting"
                  ? "Setup container running. Open openai.com/device and enter QXJM-7HPD."
                  : login.description}
              </span>
            </div>
            {status === "done" ? (
              <Badge variant="secondary">
                <CheckIcon /> Signed in
              </Badge>
            ) : status === "waiting" ? (
              <Spinner />
            ) : (
              <Button size="sm" variant="outline" onClick={() => flow.login(login.id, instanceId)}>
                Sign in
              </Button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function FirstChat({ state, card }: { state: FlowState; card: PackageCard }) {
  const [draft, setDraft] = useState("")
  const greeting =
    card.id === "coder"
      ? `I'm ${state.name || "your factory"}. I watch ${state.values.repository ?? "your repository"} for issues labeled ready-for-agent. Label one, or tell me what to pick first.`
      : `I'm ${state.name || "your new instance"}. I don't know much about you yet. What should I help with?`
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        <div className="flex gap-3">
          <InstanceAvatar
            name={state.name}
            shape={state.shape}
            color={state.color}
            size="size-8 text-sm"
          />
          <div className="max-w-prose rounded-xl bg-muted px-4 py-3 text-sm">{greeting}</div>
        </div>
      </div>
      <form className="flex gap-2 border-t p-4" onSubmit={(e) => e.preventDefault()}>
        <Input
          placeholder={`Message ${state.name || "the instance"}`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button size="icon" aria-label="Send">
          <SendIcon />
        </Button>
      </form>
    </div>
  )
}

/** Prototype-only: the current stage, a jump to any stage, and every hub call made so far. */
export function StatePanel({ flow }: { flow: Flow }) {
  const { state } = flow
  const [open, setOpen] = useState(true)
  return (
    <div className="fixed top-3 right-3 z-50 w-80 rounded-xl border-2 border-dashed border-foreground/40 bg-background/95 p-3 text-xs shadow-lg">
      <div className="flex items-center justify-between">
        <span className="font-semibold">Prototype state · {state.stage}</span>
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>
      {open && (
        <div className="mt-2 flex flex-col gap-2">
          <div className="flex flex-wrap gap-1">
            {STAGES.map((stage: Stage) => (
              <Button
                key={stage}
                size="xs"
                variant={stage === state.stage ? "default" : "outline"}
                onClick={() => flow.jump(stage)}
              >
                {stage}
              </Button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="fail"
              checked={state.failNext}
              onCheckedChange={(v) => flow.patch({ failNext: v === true })}
            />
            <Label htmlFor="fail">Fail the next preparation at “validate setup”</Label>
          </div>
          <div className="max-h-72 overflow-auto rounded-md bg-muted p-2 font-mono">
            {state.log.length === 0 && (
              <span className="text-muted-foreground">No hub calls yet.</span>
            )}
            {state.log.map((entry, i) => (
              <details key={i} className="py-0.5">
                <summary className={cn(entry.missing && "text-destructive")}>
                  {entry.method}
                  {entry.missing && " · not in contract"}
                </summary>
                <pre className="whitespace-pre-wrap">{JSON.stringify(entry.payload, null, 2)}</pre>
              </details>
            ))}
          </div>
          <Button size="xs" variant="outline" onClick={flow.reset}>
            Reset
          </Button>
        </div>
      )}
    </div>
  )
}
