import { browserClock } from "@kinby/contract"
import type {
  AvatarColor,
  AvatarShape,
  Client,
  Clock,
  InstanceCreateCommand,
  OperationState,
  OperationStep,
  SetupField,
} from "@kinby/contract"
import { useCallback, useEffect, useState } from "react"
import type * as React from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  type Creation,
  createCommand,
  followCreation,
  followStart,
  type Identity,
  type Starting,
} from "@/lib/creation"
import { followPreparation, type Preparation } from "@/lib/preparation"
import { selectInstance } from "@/lib/selection"
import { CircleCheckIcon, CircleXIcon } from "lucide-react"

const STATES: Record<OperationState, { label: string; icon: React.ReactNode }> = {
  pending: { label: "Waiting", icon: <Spinner /> },
  running: { label: "Running", icon: <Spinner /> },
  succeeded: { label: "Done", icon: <CircleCheckIcon /> },
  failed: { label: "Failed", icon: <CircleXIcon /> },
}

const SHAPES: Record<AvatarShape, string> = {
  circle: "Circle",
  squircle: "Squircle",
  square: "Square",
}

// In palette order: the hub draws an avatar nobody chose in the first one.
const COLORS: Record<AvatarColor, string> = {
  blue: "Blue",
  violet: "Violet",
  green: "Green",
  amber: "Amber",
  red: "Red",
  gray: "Gray",
}

const NEW_IDENTITY: Identity = { name: "", avatar: { shape: "circle", color: "blue" } }

type Stage = "package" | "identity" | "setup"

const HINTS: Record<Stage | "create", string> = {
  package: "Package: pick what the instance starts from.",
  identity: "Identity: name the instance and pick its avatar.",
  setup: "Setup: fill in what the image asks for.",
  create: "Create: the hub builds the instance, then you start it or leave it stopped.",
}

const noop = () => {}

/**
 * Create an instance: pick a package, name it, fill in its setup fields, create it, then start it.
 * `onPublished` hears when the hub lists the new instance.
 */
export function CreateWizard({
  caller,
  clock = browserClock,
  onPublished = noop,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
  onPublished?: () => void
}) {
  const [pick, setPick] = useState<{ package: null }>()
  const [stage, setStage] = useState<Stage>("package")
  const [identity, setIdentity] = useState(NEW_IDENTITY)
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitted, setSubmitted] = useState<InstanceCreateCommand>()

  const prepare = useCallback(
    (picked: { package: null }, report: (preparation: Preparation) => void) =>
      followPreparation(caller, picked.package, report, clock),
    [caller, clock],
  )
  const create = useCallback(
    (command: InstanceCreateCommand, report: (creation: Creation) => void) =>
      followCreation(caller, command, report, clock),
    [caller, clock],
  )
  const preparation =
    useFollowing(pick, prepare) ??
    (pick === undefined ? undefined : { state: "preparing", steps: [] })
  const creation =
    useFollowing(submitted, create) ??
    (submitted === undefined ? undefined : { state: "creating", steps: [] })

  const createdId = creation?.state === "created" ? creation.instanceId : undefined
  useEffect(() => {
    if (createdId !== undefined) onPublished()
  }, [createdId, onPublished])

  const description = preparation?.state === "prepared" ? preparation.description : undefined
  // A refused value sends the user back to the form, with each refusal on its field.
  const refused = creation?.state === "invalid" ? creation.fields : {}
  const creating = creation !== undefined && creation.state !== "invalid"

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-medium">New instance</h1>
        <p className="text-sm text-muted-foreground">{HINTS[creating ? "create" : stage]}</p>
      </header>
      {creating ? (
        <CreationView
          caller={caller}
          clock={clock}
          name={identity.name}
          creation={creation}
          onBack={() => setSubmitted(undefined)}
        />
      ) : stage === "setup" && description !== undefined ? (
        <SetupStep
          fields={description.setup_fields}
          values={values}
          errors={refused}
          onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          onBack={() => setStage("identity")}
          onCreate={() =>
            setSubmitted(createCommand(null, description.setup_fields, values, identity))
          }
        />
      ) : stage === "identity" ? (
        <IdentityStep
          identity={identity}
          onChange={setIdentity}
          onBack={() => setStage("package")}
          onContinue={() => setStage("setup")}
        />
      ) : (
        <PackageStep
          preparation={preparation}
          onPick={() => setPick({ package: null })}
          onContinue={() => setStage("identity")}
        />
      )}
    </div>
  )
}

/**
 * What following the latest request reported. A new request forgets the previous one's reports,
 * and `follow` must keep its identity across renders, or it starts over.
 */
function useFollowing<Request, Report>(
  request: Request | undefined,
  follow: (request: Request, report: (report: Report) => void) => () => void,
): Report | undefined {
  const [followed, setFollowed] = useState<{ request: Request; report: Report }>()
  useEffect(() => {
    if (request === undefined) return
    return follow(request, (report) => setFollowed({ request, report }))
  }, [request, follow])
  return request !== undefined && followed?.request === request ? followed.report : undefined
}

function PackageStep({
  preparation,
  onPick,
  onContinue,
}: {
  preparation: Preparation | undefined
  onPick: () => void
  onContinue: () => void
}) {
  return (
    <>
      <Card className="max-w-sm">
        <CardHeader>
          <CardTitle>Vanilla</CardTitle>
          <CardDescription>kinby's built-in defaults, with no package.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button
            disabled={preparation?.state === "preparing" || preparation?.state === "prepared"}
            onClick={onPick}
          >
            Prepare vanilla
          </Button>
        </CardFooter>
      </Card>
      {preparation !== undefined && (
        <section className="flex max-w-2xl flex-col gap-3">
          <h2 className="font-medium">Preparing the image</h2>
          <Progress
            label="Preparation steps"
            failure="The preparation failed"
            operation={preparation}
          />
          {preparation.state === "prepared" && (
            <>
              <h2 className="font-medium">What it asks for</h2>
              <Fields fields={preparation.description.setup_fields} />
              <div>
                <Button onClick={onContinue}>Continue</Button>
              </div>
            </>
          )}
        </section>
      )}
    </>
  )
}

function IdentityStep({
  identity,
  onChange,
  onBack,
  onContinue,
}: {
  identity: Identity
  onChange: (identity: Identity) => void
  onBack: () => void
  onContinue: () => void
}) {
  const { name, avatar } = identity
  return (
    <section className="flex max-w-md flex-col gap-6">
      <InstanceAvatar avatar={avatar} name={name} size="lg" label={`Avatar of ${name}`} />
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="instance-name">Name</FieldLabel>
          <Input
            id="instance-name"
            value={name}
            onChange={(event) => onChange({ ...identity, name: event.target.value })}
          />
        </Field>
        <FieldSet>
          <FieldLegend variant="label">Shape</FieldLegend>
          <ToggleGroup
            value={[avatar.shape]}
            onValueChange={([shape]) => {
              if (isShape(shape)) onChange({ ...identity, avatar: { ...avatar, shape } })
            }}
          >
            {Object.entries(SHAPES).map(([shape, label]) => (
              <ToggleGroupItem key={shape} value={shape} aria-label={label}>
                <InstanceAvatar avatar={{ ...avatar, shape: shape as AvatarShape }} name={name} />
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </FieldSet>
        <FieldSet>
          <FieldLegend variant="label">Color</FieldLegend>
          <ToggleGroup
            value={[avatar.color]}
            onValueChange={([color]) => {
              if (isColor(color)) onChange({ ...identity, avatar: { ...avatar, color } })
            }}
          >
            {Object.entries(COLORS).map(([color, label]) => (
              <ToggleGroupItem key={color} value={color} aria-label={label}>
                <InstanceAvatar
                  avatar={{ ...avatar, color: color as AvatarColor }}
                  name={name}
                  size="sm"
                />
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </FieldSet>
      </FieldGroup>
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button disabled={name.trim() === ""} onClick={onContinue}>
          Continue
        </Button>
      </div>
    </section>
  )
}

function isShape(value: unknown): value is AvatarShape {
  return typeof value === "string" && Object.hasOwn(SHAPES, value)
}

function isColor(value: unknown): value is AvatarColor {
  return typeof value === "string" && Object.hasOwn(COLORS, value)
}

function SetupStep({
  fields,
  values,
  errors,
  onChange,
  onBack,
  onCreate,
}: {
  fields: SetupField[]
  values: Record<string, string>
  errors: Record<string, string>
  onChange: (name: string, value: string) => void
  onBack: () => void
  onCreate: () => void
}) {
  const inputs = (kind: SetupField["kind"]) =>
    fields
      .filter((field) => field.kind === kind)
      .map((field) => (
        <SetupInput
          key={field.name}
          field={field}
          value={values[field.name] ?? ""}
          error={errors[field.name]}
          onChange={(value) => onChange(field.name, value)}
        />
      ))
  return (
    <section className="flex max-w-xl flex-col gap-6">
      <FieldGroup>
        <FieldSet>
          <FieldLegend>Configuration</FieldLegend>
          {inputs("config")}
        </FieldSet>
        <FieldSet>
          <FieldLegend>Secrets</FieldLegend>
          <FieldDescription>
            Write-only. The hub keeps them with the instance and never sends them back.
          </FieldDescription>
          {inputs("secret")}
        </FieldSet>
      </FieldGroup>
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button onClick={onCreate}>Create instance</Button>
      </div>
    </section>
  )
}

function SetupInput({
  field,
  value,
  error,
  onChange,
}: {
  field: SetupField
  value: string
  error: string | undefined
  onChange: (value: string) => void
}) {
  const id = `setup-${field.name}`
  const invalid = error !== undefined || undefined
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>
        {field.label}
        {!field.required && <span className="text-muted-foreground">(optional)</span>}
      </FieldLabel>
      {field.type === "multiline" ? (
        <Textarea
          id={id}
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          id={id}
          type={field.kind === "secret" ? "password" : "text"}
          autoComplete={field.kind === "secret" ? "new-password" : "off"}
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <FieldDescription>{field.description}</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

function CreationView({
  caller,
  clock,
  name,
  creation,
  onBack,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  creation: Exclude<Creation, { state: "invalid" }>
  onBack: () => void
}) {
  return (
    <section className="flex max-w-2xl flex-col gap-3">
      <h2 className="font-medium">Creating {name.trim()}</h2>
      <Progress label="Creation steps" failure="The creation failed" operation={creation} />
      {creation.state === "failed" && (
        <div>
          <Button variant="outline" onClick={onBack}>
            Back to setup
          </Button>
        </div>
      )}
      {creation.state === "created" && (
        <StartStep caller={caller} clock={clock} instanceId={creation.instanceId} />
      )}
    </section>
  )
}

function StartStep({
  caller,
  clock,
  instanceId,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
}) {
  const [request, setRequest] = useState<{ instanceId: string }>()
  const start = useCallback(
    (asked: { instanceId: string }, report: (starting: Starting) => void) =>
      followStart(
        caller,
        asked.instanceId,
        (starting) => {
          report(starting)
          if (starting.state === "started") selectInstance(asked.instanceId)
        },
        clock,
      ),
    [caller, clock],
  )
  const starting = useFollowing(request, start)
  const busy = request !== undefined && starting?.state !== "failed"
  return (
    <>
      <h2 className="font-medium">Start</h2>
      <p className="text-sm text-muted-foreground">The instance is listed and stopped.</p>
      {starting !== undefined && starting.state !== "started" && (
        <Progress label="Start steps" failure="The start failed" operation={starting} />
      )}
      <div className="flex gap-2">
        <Button disabled={busy} onClick={() => setRequest({ instanceId })}>
          {busy && <Spinner data-icon="inline-start" />}
          Start and chat
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => selectInstance(instanceId)}>
          Leave it stopped
        </Button>
      </div>
    </>
  )
}

/** An operation's steps, and why it failed when no step says so. */
function Progress({
  label,
  failure,
  operation,
}: {
  label: string
  failure: string
  operation: { state: string; steps: OperationStep[]; detail?: string }
}) {
  const failedOutsideAStep =
    operation.state === "failed" && !operation.steps.some((step) => step.state === "failed")
  return (
    <>
      <Steps label={label} steps={operation.steps} />
      {failedOutsideAStep && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>{failure}</AlertTitle>
          <AlertDescription>{operation.detail}</AlertDescription>
        </Alert>
      )}
    </>
  )
}

function Steps({ label, steps }: { label: string; steps: OperationStep[] }) {
  return (
    <ItemGroup aria-label={label}>
      {steps.map((step) => (
        <Item key={step.name} render={<li />} aria-label={step.name} variant="outline" size="sm">
          <ItemMedia variant="icon">{STATES[step.state].icon}</ItemMedia>
          <ItemContent>
            <ItemTitle>{step.name}</ItemTitle>
            <ItemDescription>{step.detail}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant={step.state === "failed" ? "destructive" : "secondary"}>
              {STATES[step.state].label}
            </Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

function Fields({ fields }: { fields: SetupField[] }) {
  return (
    <ItemGroup aria-label="Setup fields">
      {fields.map((field) => (
        <Item key={field.name} render={<li />} aria-label={field.label} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>{field.label}</ItemTitle>
            <ItemDescription>{field.description}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant="outline">{field.kind === "secret" ? "Secret" : "Configuration"}</Badge>
            {!field.required && <Badge variant="secondary">Optional</Badge>}
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}
