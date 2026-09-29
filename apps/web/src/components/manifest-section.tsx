import type {
  InstanceClient,
  ManifestModels,
  ModelChoice,
  NewModelPrice,
  RecapPolicy,
} from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { lastChanged } from "@/lib/config-changes"
import {
  draftOf,
  MANIFEST_FILE,
  type ManifestDraft,
  MODEL_NAME,
  type OpenedManifest,
  openManifest,
  saveManifest,
  valuesOf,
} from "@/lib/manifest"
import { reason } from "@/lib/operation"
import { CircleXIcon, PlusIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** The value a model picker holds for "no model of its own". A model name always has a colon. */
const NONE = "none"

const MODEL_FIELDS: { key: keyof ManifestModels; label: string; none?: string }[] = [
  { key: "main", label: "Main model" },
  { key: "recap", label: "Recap model", none: "Same as the main model" },
  { key: "embed", label: "Embedding model", none: "No embeddings" },
]

const BUDGET_FIELDS = [
  { key: "steps", label: "Steps", step: 1, description: "Model and tool steps in one turn." },
  { key: "tokens", label: "Tokens", step: 1, description: "Tokens in one turn." },
  { key: "seconds", label: "Seconds", step: "any", description: "Wall-clock time of one turn." },
  {
    key: "usd_per_day",
    label: "Dollars per day",
    step: "any",
    description: "Spend across every turn in a day.",
  },
] as const

const RECAP_POLICIES: Record<RecapPolicy, string> = {
  "every-turn": "Every turn",
  off: "Off",
}

/** Every IANA zone the browser knows, and UTC, which some browsers leave out. */
const TIMEZONES = [...new Set(["UTC", ...Intl.supportedValuesOf("timeZone")])]

/**
 * The settings in `kinby.toml` the app may change. A save carries the hash of what was read,
 * so a save over a change made since is refused, and "Load theirs" reads the file again.
 */
export function ManifestSection({ client }: { client: Caller }) {
  const [opened, setOpened] = useState<OpenedManifest>()
  const [draft, setDraft] = useState<ManifestDraft>()
  const [prices, setPrices] = useState<Record<string, NewModelPrice>>({})
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [refused, setRefused] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string>()

  const show = useCallback((read: OpenedManifest) => {
    setOpened(read)
    setDraft(draftOf(read.manifest.values))
    setPrices({})
    setRefused({})
  }, [])
  const load = useCallback(
    () =>
      openManifest(client).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(reason(error)),
      ),
    [client, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined || draft === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure>{failure}</Failure>
    )
  }
  const values = valuesOf(draft)
  const changed =
    Object.keys(prices).length > 0 ||
    JSON.stringify(values) !== JSON.stringify(opened.manifest.values)
  const edit = (next: ManifestDraft) => {
    setDraft(next)
    if (notice === "saved") setNotice(undefined)
  }
  const save = async () => {
    if (values === undefined) return
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await saveManifest(client, values, prices, opened.manifest.hash)
      if (saved.state === "saved") show(saved.opened)
      if (saved.state === "invalid") setRefused(saved.fields)
      else setNotice(saved.state)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setSaving(false)
    }
  }
  const choices = opened.manifest.model_choices
  const added = Object.keys(prices)
  const unnamed = Object.entries(refused).filter(([field]) => !(field in FIELD_KEYS))

  return (
    <div className="flex flex-col gap-6">
      {notice === "stale" && <StaleAlert file={MANIFEST_FILE} onLoad={() => void load()} />}
      {failure !== undefined && <Failure>{failure}</Failure>}
      {unnamed.length > 0 && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The instance refused these values</AlertTitle>
          <AlertDescription>
            {unnamed.map(([field, problem]) => `${field}: ${problem}`).join(" ")}
          </AlertDescription>
        </Alert>
      )}
      <FieldSet>
        <FieldLegend>Models</FieldLegend>
        <FieldDescription>
          Only models kinby can price are offered, so the daily budget can count them.
        </FieldDescription>
        <FieldGroup>
          {MODEL_FIELDS.map(({ key, label, none }) => (
            <ModelPicker
              key={key}
              label={label}
              none={none}
              value={draft.values.models[key]}
              choices={choices}
              added={added}
              error={refused[`models.${key}`]}
              onChange={(model) =>
                edit({
                  ...draft,
                  values: { ...draft.values, models: { ...draft.values.models, [key]: model } },
                })
              }
            />
          ))}
          <AddModel
            known={[...choices.map((choice) => choice.model), ...added]}
            onAdd={(model, price) => setPrices({ ...prices, [model]: price })}
          />
        </FieldGroup>
      </FieldSet>
      <FieldSet>
        <FieldLegend>Budgets</FieldLegend>
        <FieldDescription>Leave one blank for no limit.</FieldDescription>
        <FieldGroup>
          {BUDGET_FIELDS.map(({ key, label, step, description }) => (
            <NumberField
              key={key}
              label={label}
              description={description}
              step={step}
              value={draft.numbers[key]}
              error={refused[`budgets.${key}`]}
              onChange={(text) => edit({ ...draft, numbers: { ...draft.numbers, [key]: text } })}
            />
          ))}
        </FieldGroup>
      </FieldSet>
      <FieldSet>
        <FieldLegend>Routines</FieldLegend>
        <TimezonePicker
          value={draft.values.routines.timezone}
          error={refused["routines.timezone"]}
          onChange={(timezone) =>
            edit({ ...draft, values: { ...draft.values, routines: { timezone } } })
          }
        />
      </FieldSet>
      <FieldSet>
        <FieldLegend>Tools, memory, and feedback</FieldLegend>
        <FieldGroup>
          <SwitchField
            label="Default tools"
            description="The shell and file tools kinby ships. They apply at the next turn."
            checked={draft.values.tools.defaults}
            onChange={(defaults) =>
              edit({
                ...draft,
                values: { ...draft.values, tools: { ...draft.values.tools, defaults } },
              })
            }
          />
          <NumberField
            label="Shell timeout, seconds"
            description="How long one shell command may run."
            step={1}
            value={draft.numbers.bash_timeout_seconds}
            error={refused["tools.bash_timeout_seconds"]}
            onChange={(text) =>
              edit({ ...draft, numbers: { ...draft.numbers, bash_timeout_seconds: text } })
            }
          />
          <FieldSet>
            <FieldLegend variant="label">Recap</FieldLegend>
            <ToggleGroup
              value={[draft.values.memory.recap]}
              onValueChange={([recap]) => {
                if (recap === "every-turn" || recap === "off") {
                  edit({ ...draft, values: { ...draft.values, memory: { recap } } })
                }
              }}
            >
              {Object.entries(RECAP_POLICIES).map(([policy, label]) => (
                <ToggleGroupItem key={policy} value={policy}>
                  {label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <FieldDescription>Off keeps the trace but writes no model recap.</FieldDescription>
          </FieldSet>
          <SwitchField
            label="Ask for a rating"
            description="Ask to rate each finished turn."
            checked={draft.values.feedback.ask === "every-turn"}
            onChange={(ask) =>
              edit({
                ...draft,
                values: { ...draft.values, feedback: { ask: ask ? "every-turn" : "off" } },
              })
            }
          />
          <TextField
            label="Persona name"
            description="The name the instance answers to. Leave it blank for none."
            value={draft.values.persona_name ?? ""}
            error={refused["persona_name"]}
            onChange={(name) =>
              edit({
                ...draft,
                values: { ...draft.values, persona_name: name.trim() === "" ? null : name },
              })
            }
          />
        </FieldGroup>
      </FieldSet>
      <FieldDescription>{lastChanged(opened.lastChange)}</FieldDescription>
      <div className="flex items-center gap-3">
        <Button disabled={saving || values === undefined || !changed} onClick={() => void save()}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">
            Saved. Routines follow the timezone from the scheduler&apos;s next pass, and the rest
            applies at the next turn.
          </span>
        )}
      </div>
    </div>
  )
}

/** The refused fields each control shows next to itself. Any other is listed above the form. */
const FIELD_KEYS: Record<string, true> = Object.fromEntries(
  [
    ...MODEL_FIELDS.map(({ key }) => `models.${key}`),
    ...BUDGET_FIELDS.map(({ key }) => `budgets.${key}`),
    "routines.timezone",
    "tools.bash_timeout_seconds",
    "persona_name",
  ].map((field) => [field, true]),
)

function ModelPicker({
  label,
  none,
  value,
  choices,
  added,
  error,
  onChange,
}: {
  label: string
  none: string | undefined
  value: string | null
  choices: ModelChoice[]
  /** Models added in the form, whose prices are written at the next save. */
  added: string[]
  error: string | undefined
  onChange: (model: string | null) => void
}) {
  const id = useId()
  const invalid = error !== undefined || undefined
  const items = [
    ...(none === undefined ? [] : [{ value: NONE, label: none }]),
    ...[...choices.map(({ model }) => model), ...added].map((model) => ({
      value: model,
      label: model,
    })),
  ]
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Select
        items={items}
        value={value ?? NONE}
        onValueChange={(picked) => {
          if (typeof picked === "string") onChange(picked === NONE ? null : picked)
        }}
      >
        <SelectTrigger id={id} aria-invalid={invalid}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {none !== undefined && <SelectItem value={NONE}>{none}</SelectItem>}
            {choices.map(({ model, key_set }) => (
              <SelectItem key={model} value={model}>
                {model}
                {!key_set && <Badge variant="outline">No key</Badge>}
              </SelectItem>
            ))}
            {added.map((model) => (
              <SelectItem key={model} value={model}>
                {model}
                <Badge variant="secondary">New</Badge>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

/** A model kinby ships no price for, typed with its prices so the budget can count it. */
function AddModel({
  known,
  onAdd,
}: {
  known: string[]
  onAdd: (model: string, price: NewModelPrice) => void
}) {
  const id = useId()
  const [model, setModel] = useState("")
  const [input, setInput] = useState("")
  const [output, setOutput] = useState("")
  const price = (text: string) => (text.trim() === "" ? Number.NaN : Number(text))
  const valid =
    MODEL_NAME.test(model) && !known.includes(model) && price(input) >= 0 && price(output) >= 0
  return (
    <FieldSet>
      <FieldLegend variant="label">Add a model</FieldLegend>
      <FieldDescription>
        As provider:model, with its dollars per million input and output tokens.
      </FieldDescription>
      <div className="grid gap-3 sm:grid-cols-4 sm:items-end">
        <Field>
          <FieldLabel htmlFor={`${id}-model`}>Model</FieldLabel>
          <Input
            id={`${id}-model`}
            placeholder="mistral:mistral-large-latest"
            value={model}
            onChange={(event) => setModel(event.target.value.trim())}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-input`}>Input price</FieldLabel>
          <Input
            id={`${id}-input`}
            type="number"
            min={0}
            step="any"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-output`}>Output price</FieldLabel>
          <Input
            id={`${id}-output`}
            type="number"
            min={0}
            step="any"
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </Field>
        <Button
          variant="outline"
          disabled={!valid}
          onClick={() => {
            onAdd(model, { input: price(input), output: price(output) })
            setModel("")
            setInput("")
            setOutput("")
          }}
        >
          <PlusIcon data-icon="inline-start" />
          Add a model
        </Button>
      </div>
    </FieldSet>
  )
}

function TimezonePicker({
  value,
  error,
  onChange,
}: {
  value: string
  error: string | undefined
  onChange: (timezone: string) => void
}) {
  const id = useId()
  const invalid = error !== undefined || undefined
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>Routines timezone</FieldLabel>
      <Combobox
        items={TIMEZONES}
        value={value}
        onValueChange={(picked) => {
          if (typeof picked === "string") onChange(picked)
        }}
      >
        <ComboboxInput id={id} aria-invalid={invalid} placeholder="Search a timezone" />
        <ComboboxContent>
          <ComboboxEmpty>No timezone matches.</ComboboxEmpty>
          <ComboboxList>
            {(zone: string) => (
              <ComboboxItem key={zone} value={zone}>
                {zone}
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
      <FieldDescription>Routine schedules read in this zone.</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

function NumberField({
  label,
  description,
  step,
  value,
  error,
  onChange,
}: {
  label: string
  description: string
  step: number | "any"
  value: string
  error: string | undefined
  onChange: (text: string) => void
}) {
  const id = useId()
  const invalid = error !== undefined || undefined
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type="number"
        min={0}
        step={step}
        value={value}
        aria-invalid={invalid}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldDescription>{description}</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

function TextField({
  label,
  description,
  value,
  error,
  onChange,
}: {
  label: string
  description: string
  value: string
  error: string | undefined
  onChange: (text: string) => void
}) {
  const id = useId()
  const invalid = error !== undefined || undefined
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        value={value}
        aria-invalid={invalid}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldDescription>{description}</FieldDescription>
      <FieldError>{error}</FieldError>
    </Field>
  )
}

function SwitchField({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const id = useId()
  return (
    <Field orientation="horizontal">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <FieldContent>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <FieldDescription>{description}</FieldDescription>
      </FieldContent>
    </Field>
  )
}
