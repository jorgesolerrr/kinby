import { CallError, type Client, type FactoryResult, type FactoryRunDetail } from "@kinby/contract"
import { RotateCcwIcon, Trash2Icon } from "lucide-react"
import { useId, useState } from "react"
import { parse, stringify } from "yaml"

import { Failure, Refusal } from "@/components/config-alerts"
import { PageLink } from "@/components/factories-page"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button, buttonVariants } from "@/components/ui/button"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useHubCall } from "@/hooks/use-hub-call"
import {
  ENGINES,
  type FactoryStep,
  type Retry,
  routeText,
  type SendBack,
} from "@/lib/factory-steps"
import { workItem } from "@/lib/factory-runs"
import { navigate, runPath } from "@/lib/selection"

/** What the step editor saves through, and the factory as it was read. */
export interface StepSaving {
  client: Pick<Client, "call">
  factory: FactoryResult
}

/** Where an outcome or a failure sends the run. Null leaves a failure to the step's retry. */
type Route = string | SendBack | Retry | null

const EFFORTS = ["low", "medium", "high", "xhigh", "max"]
const DEFAULT = "default"

/** A step's card opened as its step editor: what it does, how it runs, where its failures and
 * outcomes send the run, its prompt and its hook. Opened from a run whose attempt at the step
 * failed, it also saves and retries that run on the new version. */
export function StepEditor({
  step: shown,
  earlier,
  saving,
  run,
  onSaved,
  onTurned,
  onKept,
}: {
  step: FactoryStep
  /** The steps that are on before this one. */
  earlier: FactoryStep[]
  saving: StepSaving
  /** The run the card was opened from. */
  run?: FactoryRunDetail
  /** Runs once a save, a delete or taking kinby's step made a new version. */
  onSaved: () => void
  /** Runs once turning the step off or on made a new version. */
  onTurned: () => void
  /** Runs when the hub refused to turn the step off or on, with what each step that keeps it as
   * it is needs of it, by the step's id. */
  onKept: (needs: Record<string, string>) => void
}) {
  // As the card opened, so a save from this draft is refused once the factory changed since.
  const [{ step, factory }] = useState({ step: shown, factory: saving.factory })
  const { files, declared_in: declaredIn } = factory
  const hook = step.check ?? step.call
  const hookPath = hook == null ? undefined : declaredIn[hook]
  const promptText = step.prompt === undefined ? undefined : (files[step.prompt] ?? "")
  const hookText = hookPath === undefined ? undefined : (files[hookPath] ?? "")
  const signalText = step.kind === "wait" ? stringify(step.signal) : undefined
  const [draft, setDraft] = useState(step)
  const [prompt, setPrompt] = useState(promptText)
  const [hookDraft, setHookDraft] = useState(hookText)
  const [signal, setSignal] = useState(signalText)
  const [notYaml, setNotYaml] = useState<string>()
  const { busy, refusal, failure, ask } = useHubCall()
  const turning = useHubCall()
  const id = useId()
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(step) ||
    prompt !== promptText ||
    hookDraft !== hookText ||
    signal !== signalText

  const put = (part: string, value: unknown) =>
    setDraft((last) => {
      const next = { ...last }
      if (value === undefined || value === "") delete next[part]
      else next[part] = value
      return next
    })
  const discard = () => {
    setDraft(step)
    setPrompt(promptText)
    setHookDraft(hookText)
    setSignal(signalText)
    setNotYaml(undefined)
  }
  /** Save the step, then retry the run `retrying` names, if any, on the new version. */
  const save = (retrying?: string) => {
    let saved = draft
    if (signal !== undefined) {
      try {
        saved = { ...draft, signal: parse(signal) }
      } catch (error) {
        setNotYaml(`The signal is not YAML: ${(error as Error).message}`)
        return
      }
    }
    setNotYaml(undefined)
    void ask(async () => {
      await saving.client.call("factory.step.save", {
        name: factory.name,
        hash: factory.hash,
        step: saved,
        prompt: prompt === promptText ? null : prompt,
        hook: hookDraft === hookText ? null : hookDraft,
      })
      if (retrying === undefined) return onSaved()
      await saving.client.call("factory.run.retry", { run_id: retrying, latest: true })
      navigate(runPath(retrying))
    })
  }
  const read = { name: factory.name, hash: factory.hash, step: step.id }
  const turn = (on: boolean) =>
    void turning.ask(async () => {
      try {
        await saving.client.call("factory.step.off.set", { ...read, off: !on })
      } catch (error) {
        if (error instanceof CallError) onKept(error.fields)
        throw error
      }
      onTurned()
    })
  const remove = () =>
    void ask(async () => {
      await saving.client.call("factory.step.delete", read)
      onSaved()
    })
  const takeKinbys = () =>
    void ask(async () => {
      await saving.client.call("factory.step.reset", read)
      onSaved()
    })
  const routes = [
    ...Object.entries(draft.outcomes ?? {}),
    ...(draft.failed == null ? [] : [["failed", draft.failed] as const]),
  ]
  const backTo = earlier.map((each) => ({ value: `back:${each.id}`, label: `Back to ${each.id}` }))
  const agents = earlier.filter((each) => each.kind === "agent")

  return (
    <form
      aria-label={step.id}
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        save()
      }}
    >
      <div className="flex items-start gap-4">
        <Field>
          <FieldLabel htmlFor={`${id}description`}>Description</FieldLabel>
          <Textarea
            id={`${id}description`}
            value={draft.description ?? ""}
            onChange={(event) => put("description", event.target.value)}
          />
        </Field>
        <Switch
          aria-label="On"
          className="mt-8"
          checked={step.off !== true}
          // A toggle reads the factory again, which would drop unsaved edits.
          disabled={dirty || turning.busy}
          onCheckedChange={turn}
        />
      </div>
      {turning.failure !== undefined && <Unsaved error={turning.failure} />}
      {run !== undefined && (
        <FromRun
          step={step.id}
          run={run}
          disabled={!dirty || busy}
          onSaveAndRetry={() => save(run.run.run_id)}
        />
      )}
      <FieldGroup className="grid md:grid-cols-2">
        {draft.kind === "agent" && (
          <>
            <Choice
              label="Engine"
              value={draft.engine ?? ""}
              choices={ENGINES.map((engine) => ({ value: engine, label: engine }))}
              onChange={(engine) => put("engine", engine)}
            />
            <Text label="Model" value={draft.model} onChange={(model) => put("model", model)} />
            <Choice
              label="Effort"
              value={draft.effort ?? DEFAULT}
              choices={[
                { value: DEFAULT, label: "The client's default" },
                ...EFFORTS.map((effort) => ({ value: effort, label: effort })),
              ]}
              onChange={(effort) => put("effort", effort === DEFAULT ? undefined : effort)}
            />
            <Text
              label="Timeout"
              value={draft.timeout}
              onChange={(timeout) => put("timeout", timeout)}
            />
            <Choice
              label="Resume"
              value={draft.resume ?? DEFAULT}
              choices={[
                { value: DEFAULT, label: "A new session" },
                ...agents.map((agent) => ({ value: agent.id, label: `${agent.id}'s session` })),
              ]}
              onChange={(resume) => put("resume", resume === DEFAULT ? undefined : resume)}
            />
          </>
        )}
        {draft.kind === "wait" && (
          <Text label="Quiet" value={draft.quiet} onChange={(quiet) => put("quiet", quiet)} />
        )}
        <Text label="When" value={draft.when} onChange={(when) => put("when", when)} />
        <Field>
          <FieldLabel htmlFor={`${id}retry`}>Retry</FieldLabel>
          <Input
            id={`${id}retry`}
            type="number"
            min={0}
            value={draft.retry ?? ""}
            onChange={(event) =>
              put("retry", event.target.value === "" ? undefined : Number(event.target.value))
            }
          />
        </Field>
        <RouteField
          label="When it fails"
          route={draft.failed ?? null}
          choices={[
            { value: DEFAULT, label: "Retry, then needs a human" },
            { value: "again", label: "Try this step again" },
            ...backTo,
          ]}
          onChange={(failed) => put("failed", failed ?? undefined)}
        />
        {Object.entries(draft.outcomes ?? {}).map(([outcome, target]) => (
          <RouteField
            key={outcome}
            label={`Outcome ${outcome}`}
            route={target}
            choices={[
              { value: "next", label: "Next step" },
              { value: "done", label: "Done" },
              { value: "stop", label: "Stop" },
              ...backTo,
            ]}
            onChange={(route) => put("outcomes", { ...draft.outcomes, [outcome]: route })}
          />
        ))}
      </FieldGroup>
      {routes.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Now: {routes.map(([outcome, route]) => `${outcome} → ${routeText(route)}`).join("; ")}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {prompt !== undefined && (
          <Code label={`Prompt · ${step.prompt}`} value={prompt} onChange={setPrompt} />
        )}
        {hookDraft !== undefined && (
          <Code
            label={`${step.check == null ? "Call" : "Check"} · ${hookPath}`}
            value={hookDraft}
            onChange={setHookDraft}
          />
        )}
        {signal !== undefined && <Code label="Signal" value={signal} onChange={setSignal} />}
      </div>
      {notYaml !== undefined && <Refusal reason={notYaml} />}
      {refusal !== undefined && <Refusal reason={refusal} />}
      {failure !== undefined && <Unsaved error={failure} />}
      <div className="flex flex-wrap items-end justify-between gap-4 border-t pt-3">
        <dl className="flex flex-col gap-1 text-sm">
          {[
            ["requires", step.requires?.join(", ")],
            [
              "results",
              Object.entries(step.results ?? {})
                .map(([name, type]) => `${name}: ${type}`)
                .join(", "),
            ],
          ].map(([name, values]) => (
            <div key={name} className="flex gap-3">
              <dt className="w-20 text-muted-foreground">{name}</dt>
              <dd className="font-mono">{values || "nothing"}</dd>
            </div>
          ))}
        </dl>
        <div className="flex gap-2">
          {step.off === true && (
            <Button type="button" variant="ghost" disabled={busy} onClick={remove}>
              <Trash2Icon data-icon="inline-start" />
              Delete
            </Button>
          )}
          {factory.differing_steps.includes(step.id) && (
            <Button type="button" variant="outline" disabled={busy} onClick={takeKinbys}>
              Take kinby&apos;s
            </Button>
          )}
          <Button type="button" variant="ghost" disabled={!dirty || busy} onClick={discard}>
            Discard
          </Button>
          <Button type="submit" disabled={!dirty || busy}>
            Save
          </Button>
        </div>
      </div>
    </form>
  )
}

/** The run the card was opened from, and how its last attempt at the step ended: back to its
 * page, or save the step and retry the run on the new version. */
function FromRun({
  step,
  run: { run, attempts },
  disabled,
  onSaveAndRetry,
}: {
  step: string
  run: FactoryRunDetail
  disabled: boolean
  onSaveAndRetry: () => void
}) {
  const ending = attempts.findLast((attempt) => attempt.step === step)?.ending
  return (
    <Alert layout="stack">
      <RotateCcwIcon />
      <AlertTitle>From the run on {workItem(run)}</AlertTitle>
      <AlertDescription>
        {ending != null && `Its ${step} attempt ${ending.replace("-", " ")}. `}
        The change reaches every line on the factory, and the run takes it when it retries.
      </AlertDescription>
      <AlertAction>
        <div className="flex gap-2">
          <PageLink
            href={runPath(run.run_id)}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            Back to the run
          </PageLink>
          <Button type="button" size="sm" disabled={disabled} onClick={onSaveAndRetry}>
            Save and retry
          </Button>
        </div>
      </AlertAction>
    </Alert>
  )
}

/** Why a change made no version: what the hub refused, or that it did not answer. */
export function Unsaved({ error }: { error: unknown }) {
  return error instanceof CallError ? <Refusal reason={error.message} /> : <Failure error={error} />
}

export function Text({
  label,
  value,
  onChange,
}: {
  label: string
  value: string | null | undefined
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} value={value ?? ""} onChange={(event) => onChange(event.target.value)} />
    </Field>
  )
}

export function Choice({
  label,
  value,
  choices,
  onChange,
}: {
  label: string
  value: string
  choices: { value: string; label: string }[]
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Select
        items={choices}
        value={value}
        onValueChange={(picked) => typeof picked === "string" && onChange(picked)}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {choices.map((choice) => (
            <SelectItem key={choice.value} value={choice.value}>
              {choice.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  )
}

/** A route's target, and how many times it may be taken when it sends the run back or tries the
 * step again. */
function RouteField({
  label,
  route,
  choices,
  onChange,
}: {
  label: string
  route: Route
  choices: { value: string; label: string }[]
  onChange: (route: Route) => void
}) {
  const count =
    route === null || typeof route === "string" ? 1 : "back" in route ? route.max : route.retry
  const picked =
    route === null
      ? DEFAULT
      : typeof route === "string"
        ? route
        : "back" in route
          ? `back:${route.back}`
          : "again"
  const routed = (choice: string, times: number): Route =>
    choice === DEFAULT
      ? null
      : choice === "again"
        ? { retry: times }
        : choice.startsWith("back:")
          ? { back: choice.slice("back:".length), max: times }
          : choice
  return (
    <div className="flex items-end gap-2">
      <div className="flex-1">
        <Choice
          label={label}
          value={picked}
          choices={choices}
          onChange={(choice) => onChange(routed(choice, count))}
        />
      </div>
      {route !== null && typeof route === "object" && (
        <Input
          aria-label={`${label}, up to`}
          className="w-20"
          type="number"
          min={1}
          value={count}
          onChange={(event) => onChange(routed(picked, Number(event.target.value)))}
        />
      )}
    </div>
  )
}

export function Code({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Textarea
        id={id}
        className="min-h-96"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}
