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
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  CircleCheckIcon,
  CircleXIcon,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { HubSecretPicker } from "@/components/hub-secret-picker"
import { InstanceAvatar } from "@/components/instance-avatar"
import { Progress } from "@/components/operation-progress"
import { SectionFrame } from "@/components/section-frame"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
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
import { Label } from "@/components/ui/label"
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item"
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
  BUILT_IN_FIELDS,
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

const PREPARING: Preparation = { state: "preparing", steps: [] }

const noop = () => {}

/**
 * The onboarding, on one page: while the image prepares, pick a factory role or none, name the
 * instance, pick its skills, write its prompt and fill in its setup fields. Then create it and
 * start it. `onPublished` lists the instances again. It waits for the list that follows a start
 * before it opens the instance, so the page reads that instance as running.
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
  // Each Prepare again counts, so preparing again after a failure follows a new preparation.
  const [attempt, setAttempt] = useState(1)
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
  const reasonId = useId()

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
  const preparation = useFollowing(attempt, prepare) ?? PREPARING
  const creation =
    useFollowing(submitted, create) ?? (submitted === undefined ? undefined : CREATING)
  const readSecrets = useCallback(() => caller.call("hub.secret.list", {}), [caller])
  const kept = useRead(readSecrets, clock).value?.secrets.map(({ name }) => name) ?? []

  const createdId = creation?.state === "created" ? creation.instanceId : undefined
  useEffect(() => {
    if (createdId !== undefined) void onPublished()
  }, [createdId, onPublished])

  const preparedDescription = preparation.state === "prepared" ? preparation.description : undefined
  const description = offer?.setup ?? declared ?? preparedDescription
  // A rebuilt image stores a new descriptor, then validate fails. Preparation still holds the
  // fields the user was shown, so read the stored descriptor. A role edited since it was picked is
  // refused the same way, so read its template's again.
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

  // The setup fields wait for the image, which may ask for other fields than the role does.
  const fields =
    preparation.state === "prepared" && description !== undefined ? description.setup_fields : []
  const model = values[MODEL_FIELD]
  const variable = (field: SetupField) =>
    heldVariable(field, typeof model === "string" ? model : "")
  const resolved = Object.fromEntries(
    fields
      .filter((field) => field.kind === "secret")
      .map((field) => [field.name, secretSource(sources[field.name], variable(field), kept)]),
  )
  const left = requiredLeft(fields, values, resolved)
  const blocked = blocker(
    preparation,
    identity.name,
    left,
    Object.values(resolved).some(unnamedSave),
  )

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
  // A refusal of no field shown here, such as a name another instance holds.
  const others = Object.entries(refused)
    .filter(([name]) => !fields.some((field) => field.name === name))
    .map(([, message]) => message)

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-10 border-b bg-background">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center gap-3 px-4 py-6 md:px-8">
          <InstanceAvatar
            avatar={identity.avatar}
            name={identity.name}
            size="lg"
            label={`Avatar of ${identity.name}`}
          />
          <div className="flex min-w-48 flex-1 flex-col gap-1">
            <h1 className="truncate font-heading text-xl font-semibold">
              {identity.name.trim() === "" ? "New instance" : identity.name}
            </h1>
            <ImageState
              preparation={preparation}
              onPrepare={() => {
                setDeclared(undefined)
                setAttempt((count) => count + 1)
              }}
            />
          </div>
          {!creating && (
            <div className="ml-auto flex items-center gap-3">
              {blocked !== undefined && (
                <span id={reasonId} className="text-sm text-muted-foreground">
                  {blocked}
                </span>
              )}
              <Button
                disabled={blocked !== undefined}
                aria-describedby={blocked === undefined ? undefined : reasonId}
                onClick={() =>
                  setSubmitted(
                    createCommand(
                      fields,
                      values,
                      identity,
                      { role: offer?.role, skills },
                      resolved,
                    ),
                  )
                }
              >
                Create
              </Button>
            </div>
          )}
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 py-6 md:px-8">
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
        ) : (
          <>
            {others.length > 0 && (
              <Alert variant="destructive">
                <CircleXIcon />
                <AlertTitle>The hub refused it</AlertTitle>
                <AlertDescription>{others.join(" ")}</AlertDescription>
              </Alert>
            )}
            <SectionFrame
              variant="page"
              title="Role"
              lead="A role brings its factory's hooks, tools, intake and setup fields. The instance keeps it for life."
            >
              <RoleRows caller={caller} clock={clock} offer={offer} onPick={pickRole} />
            </SectionFrame>
            <SectionFrame
              variant="page"
              title="Name and look"
              lead="The name it answers as, and the avatar it shows."
            >
              <IdentityFields identity={identity} onChange={setIdentity} />
            </SectionFrame>
            <SectionFrame
              variant="page"
              title="Skills"
              lead="Each one ticked is copied into the instance. You can add or drop skills later."
              actions={
                <span className="text-sm text-muted-foreground">{skills.length} picked</span>
              }
            >
              <SkillRows
                caller={caller}
                clock={clock}
                offer={offer}
                picked={skills}
                onChange={setSkills}
              />
            </SectionFrame>
            <SectionFrame
              variant="page"
              title="Prompt"
              lead="The instructions the instance follows in every turn."
            >
              {description === undefined ? (
                <Skeleton className="h-72 w-full" />
              ) : (
                <PromptRow
                  field={promptField}
                  owner={offer === undefined ? "kinby's default" : `${offer.role.name}'s default`}
                  value={values[PROMPT_FIELD]}
                  onChange={(value) =>
                    setValues((current) => ({ ...current, [PROMPT_FIELD]: value }))
                  }
                />
              )}
            </SectionFrame>
            <SectionFrame
              variant="page"
              title="Setup"
              lead="What the image and the role ask for. Secrets are write-only: the hub keeps them with the instance and never sends them back."
              actions={
                left > 0 && (
                  <span className="text-sm text-muted-foreground">
                    {left} required {left === 1 ? "field" : "fields"} left
                  </span>
                )
              }
            >
              {preparation.state === "prepared" ? (
                <SetupRows
                  fields={fields.filter((field) => field.name !== PROMPT_FIELD)}
                  logins={description?.logins ?? []}
                  role={offer?.role.name}
                  values={values}
                  sources={resolved}
                  errors={refused}
                  kept={kept}
                  variable={variable}
                  onChange={(name, value) =>
                    setValues((current) => ({ ...current, [name]: value }))
                  }
                  onSource={(name, source) =>
                    setSources((current) => ({ ...current, [name]: source }))
                  }
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  The setup fields show once the image is prepared.
                </p>
              )}
            </SectionFrame>
          </>
        )}
      </div>
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

/** The first thing Create waits for, or nothing once it can create. */
function blocker(
  preparation: Preparation,
  name: string,
  left: number,
  unnamed: boolean,
): string | undefined {
  if (preparation.state === "preparing") return "The image is preparing"
  if (preparation.state === "failed") return "The image did not prepare"
  if (name.trim() === "") return "Name it"
  if (left > 0) return `${left} required setup ${left === 1 ? "field" : "fields"} left`
  if (unnamed) return "Name the hub secret to save it under"
  return undefined
}

/** How many required fields hold no value and take no hub secret. */
function requiredLeft(
  fields: SetupField[],
  values: Record<string, FieldInput>,
  sources: Record<string, SecretSource>,
): number {
  return fields.filter((field) => {
    if (!field.required || sources[field.name]?.hubSecret !== undefined) return false
    const input = values[field.name] ?? initialInput(field)
    return input === undefined || (typeof input === "string" && input.trim() === "")
  }).length
}

/** How the image stands: the step its preparation runs, ready, or the step that failed and why. */
function ImageState({
  preparation,
  onPrepare,
}: {
  preparation: Preparation
  onPrepare: () => void
}) {
  if (preparation.state === "failed") {
    const failed = preparation.steps.find((step) => step.state === "failed")
    return (
      <div className="flex flex-wrap items-center gap-2">
        <p role="alert" className="text-sm text-destructive">
          {failed === undefined
            ? "The preparation failed"
            : `The preparation failed at ${failed.name}`}
          : {preparation.detail}
        </p>
        <Button variant="outline" size="sm" onClick={onPrepare}>
          Prepare again
        </Button>
      </div>
    )
  }
  if (preparation.state === "prepared") {
    return (
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground [&>svg]:size-4">
        <CircleCheckIcon />
        Image ready
      </p>
    )
  }
  const running = preparation.steps.find((step) => step.state === "running")
  return (
    <p className="flex items-center gap-1.5 text-sm text-muted-foreground [&>svg]:size-4">
      <Spinner />
      {running === undefined ? "Preparing the image" : `Preparing the image · ${running.detail}`}
    </p>
  )
}

function IdentityFields({
  identity,
  onChange,
}: {
  identity: Identity
  onChange: (identity: Identity) => void
}) {
  const { name, avatar } = identity
  return (
    <div className="flex items-start gap-4">
      <InstanceAvatar avatar={avatar} name={name} size="lg" />
      <FieldGroup className="max-w-md min-w-0 flex-1">
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
            className="flex-wrap"
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
    </div>
  )
}

function isShape(value: unknown): value is AvatarShape {
  return typeof value === "string" && Object.hasOwn(SHAPES, value)
}

function isColor(value: unknown): value is AvatarColor {
  return typeof value === "string" && Object.hasOwn(COLORS, value)
}

const roleKey = ({ factory, name }: FactoryRole) => `${factory}/${name}`

/** Pick one role of a factory in the library, or none for a vanilla instance. */
function RoleRows({
  caller,
  clock,
  offer,
  onPick,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  offer: RoleOffer | undefined
  onPick: (offer: RoleOffer | undefined) => void
}) {
  const read = useCallback(() => offeredRoles(caller), [caller])
  const { value: offers = [], failure } = useRead(read, clock)
  const pick = (next: RoleOffer | undefined) => {
    if (next !== offer) onPick(next)
  }
  return (
    <>
      {failure !== undefined && <Failure error={failure} />}
      <ul aria-label="Roles" className="flex flex-col divide-y">
        <RoleRow
          label="None"
          title="None"
          description="A vanilla instance, from kinby's built-in defaults."
          picked={offer === undefined}
          onPick={() => pick(undefined)}
        />
        {offers.map((each) => (
          <RoleRow
            key={roleKey(each.role)}
            label={`${each.role.name} in ${each.role.factory}`}
            title={
              <>
                <span className="font-mono">{each.role.name}</span>{" "}
                <span className="font-normal text-muted-foreground">in {each.role.factory}</span>
              </>
            }
            description={suggests(each.suggestedPicks.length)}
            picked={offer !== undefined && roleKey(offer.role) === roleKey(each.role)}
            onPick={() => pick(each)}
          />
        ))}
      </ul>
    </>
  )
}

function suggests(count: number): string {
  if (count === 0) return "Suggests no skills."
  return `Suggests ${count} ${count === 1 ? "skill" : "skills"}.`
}

function RoleRow({
  label,
  title,
  description,
  picked,
  onPick,
}: {
  label: string
  title: ReactNode
  description: string
  picked: boolean
  onPick: () => void
}) {
  return (
    <li>
      <Item
        size="row"
        render={<button type="button" aria-label={label} aria-pressed={picked} onClick={onPick} />}
      >
        <ItemContent className="min-w-0">
          <ItemTitle>{title}</ItemTitle>
          <ItemDescription lines="one">{description}</ItemDescription>
        </ItemContent>
        <ItemActions>{picked && <CheckIcon />}</ItemActions>
      </Item>
    </li>
  )
}

/**
 * Tick the library skills the instance copies in. A role's suggested picks come first, badged,
 * and start ticked.
 */
function SkillRows({
  caller,
  clock,
  offer,
  picked,
  onChange,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  offer: RoleOffer | undefined
  picked: string[]
  onChange: (picked: string[]) => void
}) {
  const id = useId()
  const read = useCallback(() => caller.call("library.list", {}), [caller])
  const { value: listed, failure } = useRead(read, clock)
  const offered = listed?.entries.filter((entry) => entry.kind === "skill") ?? []
  const suggested = offer?.suggestedPicks ?? []
  const ordered = [
    ...offered.filter(({ name }) => suggested.includes(name)),
    ...offered.filter(({ name }) => !suggested.includes(name)),
  ]
  return (
    <>
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : offered.length === 0 ? (
        <p className="text-sm text-muted-foreground">The library holds no skill.</p>
      ) : (
        <ul aria-label="Skills" className="flex flex-col divide-y">
          {ordered.map(({ name, description }) => (
            <li key={name} aria-label={name}>
              <Item size="row">
                <Checkbox
                  id={`${id}-${name}`}
                  checked={picked.includes(name)}
                  onCheckedChange={(on) =>
                    onChange(on ? [...picked, name] : picked.filter((each) => each !== name))
                  }
                />
                <ItemContent className="min-w-0">
                  <ItemTitle>
                    <Label htmlFor={`${id}-${name}`}>
                      <span className="truncate font-mono">{name}</span>
                    </Label>
                    {offer !== undefined && suggested.includes(name) && (
                      <Badge variant="secondary">Suggested by {offer.role.name}</Badge>
                    )}
                  </ItemTitle>
                  {description !== null && (
                    <ItemDescription lines="one">{description}</ItemDescription>
                  )}
                </ItemContent>
              </Item>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/**
 * The behavior prompt folded to its first line and whose default it starts from. It unfolds in
 * place to edit, and an edit can go back to the default.
 */
function PromptRow({
  field,
  owner,
  value,
  onChange,
}: {
  field: SetupField | undefined
  /** Whose default the prompt starts from. */
  owner: string
  value: FieldInput | undefined
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const start = field === undefined ? "" : initialInput(field)
  const initial = typeof start === "string" ? start : ""
  const text = typeof value === "string" ? value : initial
  const edited = text !== initial
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        render={
          <Item
            size="row"
            render={
              <button type="button" aria-label={`Behavior prompt, ${edited ? "edited" : owner}`} />
            }
          />
        }
      >
        <ItemContent className="min-w-0">
          <ItemTitle>
            Behavior prompt
            {edited ? (
              <Badge variant="outline">Edited</Badge>
            ) : (
              <span className="font-normal text-muted-foreground">{owner}</span>
            )}
          </ItemTitle>
          <ItemDescription lines="one">{text.split("\n")[0]}</ItemDescription>
        </ItemContent>
        <ItemActions>
          {open ? (
            <ChevronUpIcon className="text-muted-foreground" />
          ) : (
            <ChevronDownIcon className="text-muted-foreground" />
          )}
        </ItemActions>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex flex-col items-start gap-2 pb-4">
          <Textarea
            aria-label="Behavior prompt"
            className="min-h-72"
            value={text}
            onChange={(event) => onChange(event.target.value)}
          />
          {edited && (
            <Button variant="outline" size="sm" onClick={() => onChange(initial)}>
              Back to the default
            </Button>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/**
 * The setup fields as rows, each saying who asks for it, then the subscription logins the user
 * signs in to once the instance exists. Each secret field takes the hub secret named after the
 * variable it is held under until the user picks another source.
 */
function SetupRows({
  fields,
  logins,
  role,
  values,
  sources,
  errors,
  kept,
  variable,
  onChange,
  onSource,
}: {
  fields: SetupField[]
  logins: SubscriptionLogin[]
  /** The picked factory role's name, or nothing for none. */
  role: string | undefined
  values: Record<string, FieldInput>
  sources: Record<string, SecretSource>
  errors: Record<string, string>
  kept: string[]
  variable: (field: SetupField) => string
  onChange: (name: string, value: FieldInput) => void
  onSource: (name: string, source: SecretSource) => void
}) {
  const asker = (field: SetupField) =>
    BUILT_IN_FIELDS.includes(field.name) ? "every instance" : `asked by ${role ?? "the image"}`
  return (
    <ul aria-label="Setup" className="flex flex-col divide-y">
      {fields.map((field) =>
        field.kind === "secret" ? (
          <SecretRow
            key={field.name}
            field={field}
            asker={asker(field)}
            value={values[field.name] ?? initialInput(field)}
            error={errors[field.name]}
            kept={kept}
            variable={variable(field)}
            source={sources[field.name] ?? {}}
            onChange={(value) => onChange(field.name, value)}
            onSource={(source) => onSource(field.name, source)}
          />
        ) : (
          <ConfigRow
            key={field.name}
            field={field}
            asker={asker(field)}
            value={values[field.name] ?? initialInput(field)}
            error={errors[field.name]}
            onChange={(value) => onChange(field.name, value)}
          />
        ),
      )}
      {logins.map((login) => (
        <li key={login.id} aria-label={login.label}>
          <Item size="row" className="items-start">
            <ItemContent className="min-w-0 basis-56">
              <ItemTitle>{login.label}</ItemTitle>
              <ItemDescription lines="all">{login.description}</ItemDescription>
            </ItemContent>
            <div className="w-full sm:w-72">
              <Badge variant="outline">Sign in after it's created</Badge>
            </div>
          </Item>
        </li>
      ))}
    </ul>
  )
}

/** One setup field's row: its label, description and who asks for it, with its control beside. */
function SetupRow({
  field,
  asker,
  htmlFor,
  error,
  children,
}: {
  field: SetupField
  asker: string
  htmlFor: string
  error: string | undefined
  children: ReactNode
}) {
  return (
    <li>
      <Item size="row" className="items-start">
        <ItemContent className="min-w-0 basis-56">
          <ItemTitle>
            <SetupLabel field={field} htmlFor={htmlFor} />
          </ItemTitle>
          <ItemDescription lines="all">
            {field.description} · {asker}
          </ItemDescription>
        </ItemContent>
        <div className="flex w-full flex-col gap-3 sm:w-72">
          {children}
          <FieldError>{error}</FieldError>
        </div>
      </Item>
    </li>
  )
}

function ConfigRow({
  field,
  asker,
  value,
  error,
  onChange,
}: {
  field: SetupField
  asker: string
  value: FieldInput | undefined
  error: string | undefined
  onChange: (value: FieldInput) => void
}) {
  const id = `setup-${field.name}`
  const invalid = error !== undefined || undefined
  return (
    <SetupRow field={field} asker={asker} htmlFor={id} error={error}>
      {field.type === "boolean" ? (
        <Switch
          id={id}
          checked={value === true}
          aria-invalid={invalid}
          onCheckedChange={(checked) => onChange(checked)}
        />
      ) : (
        <SetupControl
          id={id}
          field={field}
          value={typeof value === "string" ? value : ""}
          invalid={invalid}
          onChange={(next) => onChange(next)}
        />
      )}
    </SetupRow>
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

export function SetupInput({
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
function SecretRow({
  field,
  asker,
  value,
  error,
  kept,
  variable,
  source,
  onChange,
  onSource,
}: {
  field: SetupField
  asker: string
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
    <SetupRow
      field={field}
      asker={asker}
      htmlFor={source.hubSecret === undefined ? id : `${id}-hub`}
      error={error}
    >
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
    </SetupRow>
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
      <h2 className="text-base font-semibold">Creating {name.trim()}</h2>
      <Progress label="Creation steps" failure="The creation failed" operation={creation} />
      {creation.state === "failed" && (
        <div>
          <Button variant="outline" onClick={onBack}>
            Back to the form
          </Button>
        </div>
      )}
      {creation.state === "created" && logins.length > 0 && (
        <>
          <h2 className="text-base font-semibold">Sign in</h2>
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
      <h2 className="text-base font-semibold">Start</h2>
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
