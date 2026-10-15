import { browserClock } from "@kinby/contract"
import type {
  AvatarColor,
  AvatarShape,
  Client,
  Clock,
  DeclaredSetup,
  FactoryRole,
  InstanceCreateCommand,
  SetupField,
  SubscriptionLogin,
} from "@kinby/contract"
import { CircleXIcon } from "lucide-react"
import { useCallback, useEffect, useId, useRef, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { HubSecretPicker } from "@/components/hub-secret-picker"
import { InstanceAvatar } from "@/components/instance-avatar"
import { Progress } from "@/components/operation-progress"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
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
  ItemTitle,
} from "@/components/ui/item"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useFollowing } from "@/hooks/use-following"
import { useRead } from "@/hooks/use-read"
import {
  type Creation,
  createCommand,
  type FieldInput,
  followCreation,
  followStart,
  heldVariable,
  type Identity,
  initialInput,
  MODEL_FIELD,
  offeredRoles,
  PROMPT_FIELD,
  proposedName,
  type RoleOffer,
  type SecretSource,
  secretSource,
  type Starting,
} from "@/lib/creation"
import { followPreparation, type Preparation } from "@/lib/preparation"
import { selectInstance } from "@/lib/selection"

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

const CREATING: Creation = { state: "creating", steps: [] }

type Stage = "image" | "role" | "identity" | "skills" | "prompt" | "setup"

const HINTS: Record<Stage | "create", string> = {
  image: "Image: prepare the image the instance runs on.",
  role: "Factory role: pick the part the instance plays in a factory, or none.",
  identity: "Identity: name the instance and pick its avatar.",
  skills: "Skills: pick the library skills the instance starts with.",
  prompt: "Prompt: write the instructions the instance follows in every turn.",
  setup: "Setup: fill in what the image or the role asks for.",
  create:
    "Create: the hub builds the instance. Sign in to what it declares, then start it or leave " +
    "it stopped.",
}

const noop = () => {}

/**
 * The onboarding: prepare the image, pick a factory role or none, name the instance, pick its
 * skills, write its prompt, fill in its setup fields, create it, then start it. `onPublished`
 * lists the instances again. It waits for the list that follows a start before it opens the
 * instance, so the page reads that instance as running.
 */
export function Onboarding({
  caller,
  clock = browserClock,
  onPublished = noop,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
  onPublished?: () => void | Promise<void>
}) {
  // Each click on Prepare counts, so preparing again after a failure follows a new preparation.
  const [attempt, setAttempt] = useState<number>()
  const [stage, setStage] = useState<Stage>("image")
  const [offer, setOffer] = useState<RoleOffer>()
  const [skills, setSkills] = useState<string[]>([])
  const [identity, setIdentity] = useState(NEW_IDENTITY)
  const [values, setValues] = useState<Record<string, FieldInput>>({})
  const [sources, setSources] = useState<Record<string, SecretSource>>({})
  const [submitted, setSubmitted] = useState<InstanceCreateCommand>()
  const [declared, setDeclared] = useState<DeclaredSetup>()
  // A later read wins. The one from the failed validate can still be in flight when
  // the hub answers invalid_setup.
  const declarationRead = useRef(0)

  const prepare = useCallback(
    (_: number, report: (preparation: Preparation) => void) =>
      followPreparation(caller, report, clock),
    [caller, clock],
  )
  const create = useCallback(
    (command: InstanceCreateCommand, report: (creation: Creation) => void) =>
      followCreation(caller, command, report, clock),
    [caller, clock],
  )
  const preparation =
    useFollowing(attempt, prepare) ??
    (attempt === undefined ? undefined : { state: "preparing", steps: [] })
  const creation =
    useFollowing(submitted, create) ?? (submitted === undefined ? undefined : CREATING)

  const createdId = creation?.state === "created" ? creation.instanceId : undefined
  useEffect(() => {
    if (createdId !== undefined) void onPublished()
  }, [createdId, onPublished])

  const preparedDescription =
    preparation?.state === "prepared" ? preparation.description : undefined
  const description = offer?.setup ?? declared ?? preparedDescription
  // A rebuilt image stores a new descriptor, then validate fails. Preparation still holds the
  // fields the user was shown, and Prepare stays disabled, so read the stored descriptor. A role
  // edited since it was picked is refused the same way, so read its template's again.
  useEffect(() => {
    if (submitted === undefined || !setupWasRefused(creation)) return
    const request = ++declarationRead.current
    const role = submitted.factory_role
    const reread =
      role == null
        ? caller.call("image.describe", {}).then((next) => () => setDeclared(next))
        : caller.call("factory.describe", { name: role.factory }).then(({ instances }) => () => {
            const setup = instances[role.name]
            if (setup === undefined) return
            setOffer((current) =>
              current !== undefined && roleKey(current.role) === roleKey(role)
                ? { ...current, setup }
                : current,
            )
          })
    void reread.then(
      (store) => {
        if (request === declarationRead.current) store()
      },
      // Keep the fields already on the form if the hub cannot be read.
      () => {},
    )
  }, [creation, caller, submitted])
  // A refused value sends the user back to the form, with each refusal on its field.
  const refused = creation?.state === "invalid" ? creation.fields : {}
  const creating = creation !== undefined && creation.state !== "invalid"

  // Another role brings its own suggested picks, prompt and fields, and its name if the user
  // kept the one proposed.
  const pickRole = (next: RoleOffer | undefined) => {
    setIdentity((current) =>
      current.name === proposedName(offer?.role)
        ? { ...current, name: proposedName(next?.role) }
        : current,
    )
    setOffer(next)
    setSkills(next?.suggestedPicks ?? [])
    setValues({})
    setSources({})
  }
  const promptField = description?.setup_fields.find((field) => field.name === PROMPT_FIELD)

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
          logins={description?.logins ?? []}
          creation={creation}
          onBack={() => setSubmitted(undefined)}
          onPublished={onPublished}
        />
      ) : stage === "setup" && description !== undefined ? (
        <SetupStep
          caller={caller}
          clock={clock}
          fields={description.setup_fields.filter((field) => field.name !== PROMPT_FIELD)}
          values={values}
          sources={sources}
          errors={refused}
          onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          onSource={(name, source) => setSources((current) => ({ ...current, [name]: source }))}
          onBack={() => setStage("prompt")}
          onCreate={(resolved) =>
            setSubmitted(
              createCommand(
                description.setup_fields,
                values,
                identity,
                { role: offer?.role, skills },
                resolved,
              ),
            )
          }
        />
      ) : stage === "prompt" ? (
        <PromptStep
          field={promptField}
          value={values[PROMPT_FIELD]}
          onChange={(value) => setValues((current) => ({ ...current, [PROMPT_FIELD]: value }))}
          onBack={() => setStage("skills")}
          onContinue={() => setStage("setup")}
        />
      ) : stage === "skills" ? (
        <SkillsStep
          caller={caller}
          clock={clock}
          picked={skills}
          onChange={setSkills}
          onBack={() => setStage("identity")}
          onContinue={() => setStage("prompt")}
        />
      ) : stage === "identity" ? (
        <IdentityStep
          identity={identity}
          onChange={setIdentity}
          onBack={() => setStage("role")}
          onContinue={() => setStage("skills")}
        />
      ) : stage === "role" ? (
        <RoleStep
          caller={caller}
          clock={clock}
          offer={offer}
          onPick={pickRole}
          onBack={() => setStage("image")}
          onContinue={() => setStage("identity")}
        />
      ) : (
        <ImageStep
          preparation={
            preparation?.state === "prepared" && declared !== undefined
              ? { ...preparation, description: declared }
              : preparation
          }
          onPrepare={() => {
            setDeclared(undefined)
            setAttempt((count) => (count ?? 0) + 1)
          }}
          onContinue={() => setStage("role")}
        />
      )}
    </div>
  )
}

/** The hub refused the setup values, or the image it just built no longer takes them. */
function setupWasRefused(creation: Creation | undefined): boolean {
  if (creation?.state === "invalid") return true
  return (
    creation?.state === "failed" &&
    creation.steps.some((step) => step.name === "validate" && step.state === "failed")
  )
}

function ImageStep({
  preparation,
  onPrepare,
  onContinue,
}: {
  preparation: Preparation | undefined
  onPrepare: () => void
  onContinue: () => void
}) {
  const busy = preparation?.state === "preparing" || preparation?.state === "prepared"
  return (
    <>
      <p className="max-w-2xl text-sm text-muted-foreground">
        The hub builds kinby's image from its own checkout, or reuses the one it built, then reads
        what an instance on it asks for.
      </p>
      <div>
        <Button disabled={busy} onClick={onPrepare}>
          Prepare the image
        </Button>
      </div>
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
              <Logins logins={preparation.description.logins ?? []} />
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

const NO_ROLE = "none"

const roleKey = ({ factory, name }: FactoryRole) => `${factory}/${name}`

/** Pick one role of a factory in the library, or none for a vanilla instance. */
function RoleStep({
  caller,
  clock,
  offer,
  onPick,
  onBack,
  onContinue,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  offer: RoleOffer | undefined
  onPick: (offer: RoleOffer | undefined) => void
  onBack: () => void
  onContinue: () => void
}) {
  const id = useId()
  const read = useCallback(() => offeredRoles(caller), [caller])
  const { value: offers = [], failure } = useRead(read, clock)
  const items = [
    { value: NO_ROLE, label: "None: a vanilla instance" },
    ...offers.map(({ role }) => ({
      value: roleKey(role),
      label: `${role.name} in ${role.factory}`,
    })),
  ]
  return (
    <section className="flex max-w-md flex-col gap-6">
      {failure !== undefined && <Failure error={failure} />}
      <Field>
        <FieldLabel htmlFor={id}>Factory role</FieldLabel>
        <Select
          items={items}
          value={offer === undefined ? NO_ROLE : roleKey(offer.role)}
          onValueChange={(picked) => onPick(offers.find(({ role }) => roleKey(role) === picked))}
        >
          <SelectTrigger id={id}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map(({ value, label }) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <FieldDescription>
          A role brings its factory&apos;s hooks, tools, intake and setup fields. The instance keeps
          it for life.
        </FieldDescription>
      </Field>
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button onClick={onContinue}>Continue</Button>
      </div>
    </section>
  )
}

/** Tick the library skills the instance copies in. A role's suggested picks start ticked. */
function SkillsStep({
  caller,
  clock,
  picked,
  onChange,
  onBack,
  onContinue,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  picked: string[]
  onChange: (picked: string[]) => void
  onBack: () => void
  onContinue: () => void
}) {
  const id = useId()
  const read = useCallback(() => caller.call("library.list", {}), [caller])
  const { value: listed, failure } = useRead(read, clock)
  const offered = listed?.entries.filter((entry) => entry.kind === "skill") ?? []
  return (
    <section className="flex max-w-xl flex-col gap-6">
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : (
        <FieldSet>
          <FieldLegend>Skills</FieldLegend>
          <FieldDescription>
            {offered.length === 0
              ? "The library holds no skill."
              : "Each one ticked is copied into the instance."}
          </FieldDescription>
          <FieldGroup>
            {offered.map(({ name, description }) => (
              <Field key={name} orientation="horizontal">
                <Checkbox
                  id={`${id}-${name}`}
                  checked={picked.includes(name)}
                  onCheckedChange={(on) =>
                    onChange(on ? [...picked, name] : picked.filter((each) => each !== name))
                  }
                />
                <FieldContent>
                  <FieldLabel htmlFor={`${id}-${name}`}>{name}</FieldLabel>
                  {description !== null && <FieldDescription>{description}</FieldDescription>}
                </FieldContent>
              </Field>
            ))}
          </FieldGroup>
        </FieldSet>
      )}
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button onClick={onContinue}>Continue</Button>
      </div>
    </section>
  )
}

/** The behavior prompt, starting from the role's or kinby's default, for the user to edit. */
function PromptStep({
  field,
  value,
  onChange,
  onBack,
  onContinue,
}: {
  field: SetupField | undefined
  value: FieldInput | undefined
  onChange: (value: string) => void
  onBack: () => void
  onContinue: () => void
}) {
  const id = useId()
  const start = field === undefined ? "" : initialInput(field)
  const text = value ?? start
  return (
    <section className="flex max-w-2xl flex-col gap-6">
      <Field>
        <FieldLabel htmlFor={id}>Behavior prompt</FieldLabel>
        <Textarea
          id={id}
          className="min-h-72"
          value={typeof text === "string" ? text : ""}
          onChange={(event) => onChange(event.target.value)}
        />
        <FieldDescription>{field?.description}</FieldDescription>
      </Field>
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button onClick={onContinue}>Continue</Button>
      </div>
    </section>
  )
}

/**
 * The setup fields. Each secret field takes the hub secret named after the variable it is held
 * under until the user picks another source.
 */
function SetupStep({
  caller,
  clock,
  fields,
  values,
  sources,
  errors,
  onChange,
  onSource,
  onBack,
  onCreate,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  fields: SetupField[]
  values: Record<string, FieldInput>
  sources: Record<string, SecretSource>
  errors: Record<string, string>
  onChange: (name: string, value: FieldInput) => void
  onSource: (name: string, source: SecretSource) => void
  onBack: () => void
  onCreate: (sources: Record<string, SecretSource>) => void
}) {
  const read = useCallback(() => caller.call("hub.secret.list", {}), [caller])
  const kept = useRead(read, clock).value?.secrets.map(({ name }) => name) ?? []
  const model = values[MODEL_FIELD]
  const variable = (field: SetupField) =>
    heldVariable(field, typeof model === "string" ? model : "")
  const resolved = Object.fromEntries(
    fields
      .filter((field) => field.kind === "secret")
      .map((field) => [field.name, secretSource(sources[field.name], variable(field), kept)]),
  )
  const unnamed = Object.values(resolved).some(unnamedSave)
  const inputs = (kind: SetupField["kind"]) =>
    fields
      .filter((field) => field.kind === kind)
      .map((field) =>
        kind === "secret" ? (
          <SecretInput
            key={field.name}
            field={field}
            value={values[field.name] ?? initialInput(field)}
            error={errors[field.name]}
            kept={kept}
            variable={variable(field)}
            source={resolved[field.name] ?? {}}
            onChange={(value) => onChange(field.name, value)}
            onSource={(source) => onSource(field.name, source)}
          />
        ) : (
          <SetupInput
            key={field.name}
            field={field}
            value={values[field.name] ?? initialInput(field)}
            error={errors[field.name]}
            onChange={(value) => onChange(field.name, value)}
          />
        ),
      )
  // A refusal of no field shown here, such as a name another instance holds.
  const others = Object.entries(errors)
    .filter(([name]) => !fields.some((field) => field.name === name))
    .map(([, message]) => message)
  return (
    <section className="flex max-w-xl flex-col gap-6">
      {others.length > 0 && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The hub refused it</AlertTitle>
          <AlertDescription>{others.join(" ")}</AlertDescription>
        </Alert>
      )}
      <FieldGroup>
        <FieldSet>
          <FieldLegend>Configuration</FieldLegend>
          {inputs("config")}
        </FieldSet>
        <FieldSet>
          <FieldLegend>Secrets</FieldLegend>
          <FieldDescription>
            Write-only. The hub keeps them with the instance and never sends them back. A field can
            take a hub secret instead.
          </FieldDescription>
          {inputs("secret")}
        </FieldSet>
      </FieldGroup>
      <div className="flex gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button disabled={unnamed} onClick={() => onCreate(resolved)}>
          Create instance
        </Button>
      </div>
    </section>
  )
}

function SetupLabel({ field, htmlFor }: { field: SetupField; htmlFor: string }) {
  return (
    <FieldLabel htmlFor={htmlFor}>
      {field.label}
      {!field.required && <span className="text-muted-foreground">(optional)</span>}
    </FieldLabel>
  )
}

function SetupInput({
  field,
  value,
  error,
  onChange,
}: {
  field: SetupField
  value: FieldInput | undefined
  error: string | undefined
  onChange: (value: FieldInput) => void
}) {
  const id = `setup-${field.name}`
  const invalid = error !== undefined || undefined
  const label = <SetupLabel field={field} htmlFor={id} />
  if (field.type === "boolean") {
    return (
      <Field orientation="horizontal" data-invalid={invalid}>
        <Switch
          id={id}
          checked={value === true}
          aria-invalid={invalid}
          onCheckedChange={(checked) => onChange(checked)}
        />
        <FieldContent>
          {label}
          <FieldDescription>{field.description}</FieldDescription>
          <FieldError>{error}</FieldError>
        </FieldContent>
      </Field>
    )
  }
  const text = typeof value === "string" ? value : ""
  return (
    <Field data-invalid={invalid}>
      {label}
      <SetupControl
        id={id}
        field={field}
        value={text}
        invalid={invalid}
        onChange={(next) => onChange(next)}
      />
      <FieldDescription>{field.description}</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

const unnamedSave = (source: SecretSource) => source.save?.trim() === ""

/**
 * A secret field: a hub secret it takes, or a value typed, which can be saved to the hub under a
 * name that starts as the field's variable.
 */
function SecretInput({
  field,
  value,
  error,
  kept,
  variable,
  source,
  onChange,
  onSource,
}: {
  field: SetupField
  value: FieldInput | undefined
  error: string | undefined
  kept: string[]
  variable: string
  source: SecretSource
  onChange: (value: FieldInput) => void
  onSource: (source: SecretSource) => void
}) {
  const id = `setup-${field.name}`
  const invalid = error !== undefined || undefined
  return (
    <Field data-invalid={invalid}>
      <SetupLabel field={field} htmlFor={source.hubSecret === undefined ? id : `${id}-hub`} />
      {kept.length > 0 && (
        <HubSecretPicker
          id={`${id}-hub`}
          label={field.label}
          kept={kept}
          picked={source.hubSecret}
          invalid={invalid}
          onPick={(picked) => onSource(picked === undefined ? {} : { hubSecret: picked })}
        />
      )}
      {source.hubSecret === undefined && (
        <>
          <SetupControl
            id={id}
            field={field}
            value={typeof value === "string" ? value : ""}
            invalid={invalid}
            onChange={(next) => {
              onChange(next)
              // Hub secrets read later must not take over a field the user typed into.
              onSource(source)
            }}
          />
          <Field orientation="horizontal">
            <Checkbox
              id={`${id}-save`}
              checked={source.save !== undefined}
              onCheckedChange={(on) => onSource(on ? { save: variable } : {})}
            />
            <FieldLabel htmlFor={`${id}-save`}>Save to the hub</FieldLabel>
          </Field>
          {source.save !== undefined && (
            <Field data-invalid={unnamedSave(source) || undefined}>
              <FieldLabel htmlFor={`${id}-name`}>Name on the hub</FieldLabel>
              <Input
                id={`${id}-name`}
                autoComplete="off"
                value={source.save}
                aria-invalid={unnamedSave(source) || undefined}
                onChange={(event) => onSource({ save: event.target.value })}
              />
              <FieldError>
                {unnamedSave(source) ? "Name the hub secret to save it under." : undefined}
              </FieldError>
            </Field>
          )}
        </>
      )}
      <FieldDescription>{field.description}</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

/** The control for a field whose value is text: typed, picked from its choices, or a number. */
function SetupControl({
  id,
  field,
  value,
  invalid,
  onChange,
}: {
  id: string
  field: SetupField
  value: string
  invalid: true | undefined
  onChange: (value: string) => void
}) {
  switch (field.type) {
    case "choice":
      return (
        <Select
          value={value === "" ? null : value}
          onValueChange={(picked) => onChange(typeof picked === "string" ? picked : "")}
        >
          <SelectTrigger id={id} aria-invalid={invalid}>
            <SelectValue placeholder="Pick one" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {(field.choices ?? []).map((choice) => (
                <SelectItem key={choice} value={choice}>
                  {choice}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      )
    case "multiline":
      return (
        <Textarea
          id={id}
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      )
    case "integer":
      return (
        <Input
          id={id}
          type="number"
          step={1}
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      )
    case "email":
    case "url":
      return (
        <Input
          id={id}
          type={field.type}
          autoComplete="off"
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      )
    default:
      return (
        <Input
          id={id}
          type={field.kind === "secret" ? "password" : "text"}
          autoComplete={field.kind === "secret" ? "new-password" : "off"}
          value={value}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      )
  }
}

function CreationView({
  caller,
  clock,
  name,
  logins,
  creation,
  onBack,
  onPublished,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  logins: SubscriptionLogin[]
  creation: Exclude<Creation, { state: "invalid" }>
  onBack: () => void
  onPublished: () => void | Promise<void>
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
      {creation.state === "created" && logins.length > 0 && (
        <>
          <h2 className="font-medium">Sign in</h2>
          <p className="text-sm text-muted-foreground">
            Each sign-in shows a link and a code. Open the link and enter the code there. Starting
            the instance does not wait for a sign-in.
          </p>
          <SubscriptionLogins
            caller={caller}
            clock={clock}
            instanceId={creation.instanceId}
            // The hub seeds each declared login pending when it publishes the instance.
            logins={logins.map((login) => ({ ...login, state: "pending" }))}
          />
        </>
      )}
      {creation.state === "created" && (
        <StartStep
          caller={caller}
          clock={clock}
          instanceId={creation.instanceId}
          onPublished={onPublished}
        />
      )}
    </section>
  )
}

function StartStep({
  caller,
  clock,
  instanceId,
  onPublished,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  onPublished: () => void | Promise<void>
}) {
  const [request, setRequest] = useState<{ instanceId: string }>()
  const start = useCallback(
    (asked: { instanceId: string }, report: (starting: Starting) => void) =>
      followStart(
        caller,
        asked.instanceId,
        (starting) => {
          report(starting)
          if (starting.state === "started") {
            void Promise.resolve(onPublished()).then(() => selectInstance(asked.instanceId))
          }
        },
        clock,
      ),
    [caller, clock, onPublished],
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

function Fields({ fields }: { fields: SetupField[] }) {
  return (
    <ItemGroup aria-label="Setup fields">
      {fields.map((field) => (
        <Item key={field.name} render={<li />} aria-label={field.label} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>{field.label}</ItemTitle>
            <ItemDescription lines="all">{field.description}</ItemDescription>
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

/** The subscription logins the image declares. The user signs in once the instance exists. */
function Logins({ logins }: { logins: SubscriptionLogin[] }) {
  if (logins.length === 0) return null
  return (
    <ItemGroup aria-label="Subscription logins">
      {logins.map((login) => (
        <Item key={login.id} render={<li />} aria-label={login.label} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>{login.label}</ItemTitle>
            <ItemDescription lines="all">{login.description}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant="outline">Sign-in</Badge>
            <Badge variant="secondary">After creation</Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}
