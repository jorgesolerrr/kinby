import { browserClock } from "@kinby/contract"
import type {
  AvatarColor,
  AvatarShape,
  Client,
  Clock,
  CuratedPackage,
  InstanceCreateCommand,
  OperationStep,
  PackageDescription,
  PackageSelection,
  SetupField,
  SubscriptionLogin,
} from "@kinby/contract"
import { useCallback, useEffect, useRef, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { OperationSteps } from "@/components/operation-steps"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  ItemMedia,
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
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useFollowing } from "@/hooks/use-following"
import {
  type Creation,
  createCommand,
  type FieldInput,
  followCreation,
  followStart,
  type Identity,
  initialInput,
  type Starting,
} from "@/lib/creation"
import { followPreparation, type Preparation } from "@/lib/preparation"
import { selectInstance } from "@/lib/selection"
import { CircleXIcon, CodeIcon, PackageIcon, SparklesIcon, type LucideIcon } from "lucide-react"

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

type Stage = "package" | "identity" | "setup"

const HINTS: Record<Stage | "create", string> = {
  package: "Package: pick what the instance starts from.",
  identity: "Identity: name the instance and pick its avatar.",
  setup: "Setup: fill in what the image asks for.",
  create:
    "Create: the hub builds the instance. Sign in to what it declares, then start it or leave " +
    "it stopped.",
}

const noop = () => {}

/** The icons the curated entries name. An icon the app does not know draws as a package. */
const ICONS: Record<string, LucideIcon> = { code: CodeIcon }

type CuratedList =
  | { state: "loading" }
  | { state: "failed"; detail: string }
  | { state: "loaded"; packages: CuratedPackage[] }

type PackagePick = { package: PackageSelection | null }

/**
 * Create an instance: pick a package, name it, fill in its setup fields, create it, then start it.
 * `onPublished` lists the instances again. The wizard waits for the list that follows a start
 * before it opens the instance, so the page reads that instance as running.
 */
export function CreateWizard({
  caller,
  clock = browserClock,
  onPublished = noop,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
  onPublished?: () => void | Promise<void>
}) {
  const curated = useCuratedList(caller)
  const [pick, setPick] = useState<PackagePick>()
  const [stage, setStage] = useState<Stage>("package")
  const [identity, setIdentity] = useState(NEW_IDENTITY)
  const [values, setValues] = useState<Record<string, FieldInput>>({})
  const [submitted, setSubmitted] = useState<InstanceCreateCommand>()
  const [declared, setDeclared] = useState<PackageDescription>()
  // A later read wins. The one from the failed validate can still be in flight when
  // the hub answers invalid_setup.
  const declarationRead = useRef(0)

  const prepare = useCallback(
    (picked: PackagePick, report: (preparation: Preparation) => void) =>
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
    useFollowing(submitted, create) ?? (submitted === undefined ? undefined : CREATING)

  const createdId = creation?.state === "created" ? creation.instanceId : undefined
  useEffect(() => {
    if (createdId !== undefined) void onPublished()
  }, [createdId, onPublished])

  const preparedDescription =
    preparation?.state === "prepared" ? preparation.description : undefined
  const description = declared ?? preparedDescription
  // A rebuilt image stores a new descriptor, then validate fails. Preparation still holds the
  // fields the user was shown, and Prepare stays disabled, so read the stored descriptor.
  useEffect(() => {
    if (pick === undefined || submitted?.package !== pick.package || !setupWasRefused(creation)) {
      return
    }
    const selection = pick.package
    const request = ++declarationRead.current
    void caller.call("package.describe", { package: selection }).then(
      (next) => {
        if (request === declarationRead.current) setDeclared(next)
      },
      // Keep the fields already on the form if the hub cannot be read.
      () => {},
    )
  }, [creation, caller, pick, submitted])
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
          logins={description?.logins ?? []}
          creation={creation}
          onBack={() => setSubmitted(undefined)}
          onPublished={onPublished}
        />
      ) : stage === "setup" && description !== undefined ? (
        <SetupStep
          fields={description.setup_fields}
          values={values}
          errors={refused}
          onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          onBack={() => setStage("identity")}
          onCreate={() =>
            setSubmitted(
              createCommand(pick?.package ?? null, description.setup_fields, values, identity),
            )
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
          curated={curated}
          preparation={
            preparation?.state === "prepared" && declared !== undefined
              ? { ...preparation, description: declared }
              : preparation
          }
          busy={(selection) =>
            pick?.package === selection &&
            (preparation?.state === "preparing" || preparation?.state === "prepared")
          }
          onPick={(selection) => {
            setDeclared(undefined)
            setPick({ package: selection })
          }}
          onContinue={() => setStage("identity")}
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

/** The packages the hub curates, read once when the wizard opens. */
function useCuratedList(caller: Pick<Client, "call">): CuratedList {
  const [curated, setCurated] = useState<CuratedList>({ state: "loading" })
  useEffect(() => {
    let current = true
    caller.call("package.list", {}).then(
      ({ packages }) => {
        if (current) setCurated({ state: "loaded", packages })
      },
      (error: unknown) => {
        if (current) {
          setCurated({
            state: "failed",
            detail: error instanceof Error ? error.message : String(error),
          })
        }
      },
    )
    return () => {
      current = false
    }
  }, [caller])
  return curated
}

/** A pinned commit reads as its short SHA, an index version as itself. */
function versionOf(selection: PackageSelection): string {
  return typeof selection.version === "string"
    ? selection.version
    : selection.version.sha.slice(0, 7)
}

function PackageCard({
  name,
  description,
  icon: Icon,
  version,
  action,
  disabled,
  onPick,
}: {
  name: string
  description: string
  icon: LucideIcon
  version?: string
  action: string
  disabled: boolean
  onPick: () => void
}) {
  return (
    <Item render={<li />} aria-label={name} variant="outline">
      <ItemMedia variant="icon">
        <Icon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          {name}
          {version !== undefined && <Badge variant="outline">{version}</Badge>}
        </ItemTitle>
        <ItemDescription lines="all">{description}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button disabled={disabled} onClick={onPick}>
          {action}
        </Button>
      </ItemActions>
    </Item>
  )
}

function PackageStep({
  curated,
  preparation,
  busy,
  onPick,
  onContinue,
}: {
  curated: CuratedList
  preparation: Preparation | undefined
  busy: (selection: PackageSelection | null) => boolean
  onPick: (selection: PackageSelection | null) => void
  onContinue: () => void
}) {
  return (
    <>
      <ItemGroup aria-label="Packages" className="max-w-2xl">
        <PackageCard
          name="Vanilla"
          description="kinby's built-in defaults, with no package."
          icon={SparklesIcon}
          action="Prepare vanilla"
          disabled={busy(null)}
          onPick={() => onPick(null)}
        />
        {curated.state === "loaded" &&
          curated.packages.map((entry) => (
            <PackageCard
              key={entry.id}
              name={entry.display_name}
              description={entry.description}
              icon={ICONS[entry.icon] ?? PackageIcon}
              version={versionOf(entry.selection)}
              action={`Prepare ${entry.display_name}`}
              disabled={busy(entry.selection)}
              onPick={() => onPick(entry.selection)}
            />
          ))}
      </ItemGroup>
      {curated.state === "failed" && (
        <Alert variant="destructive" className="max-w-2xl">
          <CircleXIcon />
          <AlertTitle>The curated packages could not be listed</AlertTitle>
          <AlertDescription>{curated.detail}</AlertDescription>
        </Alert>
      )}
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

function SetupStep({
  fields,
  values,
  errors,
  onChange,
  onBack,
  onCreate,
}: {
  fields: SetupField[]
  values: Record<string, FieldInput>
  errors: Record<string, string>
  onChange: (name: string, value: FieldInput) => void
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
          value={values[field.name] ?? initialInput(field)}
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
  value: FieldInput | undefined
  error: string | undefined
  onChange: (value: FieldInput) => void
}) {
  const id = `setup-${field.name}`
  const invalid = error !== undefined || undefined
  const label = (
    <FieldLabel htmlFor={id}>
      {field.label}
      {!field.required && <span className="text-muted-foreground">(optional)</span>}
    </FieldLabel>
  )
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
      <OperationSteps label={label} steps={operation.steps} />
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

/** The subscription logins a package declares. The user signs in once the instance exists. */
function Logins({ logins }: { logins: SubscriptionLogin[] }) {
  if (logins.length === 0) return null
  return (
    <ItemGroup aria-label="Subscription logins">
      {logins.map((login) => (
        <Item key={login.id} render={<li />} aria-label={login.label} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>{login.label}</ItemTitle>
            <ItemDescription>{login.description}</ItemDescription>
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
