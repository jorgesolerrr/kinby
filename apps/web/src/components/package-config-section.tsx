import type { Clock, InstanceClient } from "@kinby/contract"
import { useCallback, useEffect, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
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
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { lastChanged } from "@/lib/config-changes"
import { retried } from "@/lib/operation"
import {
  type FormField,
  type OpenedPackageConfig,
  openPackageConfig,
  PACKAGE_CONFIG_FILE,
  savePackageConfig,
} from "@/lib/package-config"
import { BoxIcon, XIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/**
 * The package's config as a form built from the model the package declares. The package's
 * validator judges a save, and its reasons show beside their fields. `onSaved` hears each save.
 */
export function PackageConfigSection({
  client,
  clock,
  onSaved,
}: {
  client: Caller
  clock: Clock
  onSaved: () => void
}) {
  const pacing = usePace(clock)
  const [opened, setOpened] = useState<OpenedPackageConfig | null>()
  const [fields, setFields] = useState<FormField[]>([])
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()

  const show = useCallback((read: OpenedPackageConfig | null) => {
    setOpened(read)
    setFields(read?.fields ?? [])
    setErrors({})
  }, [])
  const load = useCallback(
    () =>
      retried(() => openPackageConfig(client), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, pacing, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  if (opened === null) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <BoxIcon />
          </EmptyMedia>
          <EmptyTitle>No package config</EmptyTitle>
          <EmptyDescription>
            This instance runs no package, or its package declares no config.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  const change = (next: FormField[]) => {
    setFields(next)
    setErrors({})
    if (notice === "saved") setNotice(undefined)
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await savePackageConfig(client, fields, opened.hash)
      if (saved.state === "invalid") {
        setErrors(saved.fields)
        return
      }
      if (saved.state === "saved") {
        show(saved.opened)
        onSaved()
      }
      setNotice(saved.state)
    } catch (error) {
      setFailure(error)
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="flex flex-col gap-6">
      {notice === "stale" && <StaleAlert file={PACKAGE_CONFIG_FILE} onLoad={() => void load()} />}
      {failure !== undefined && <Failure error={failure} />}
      <FieldGroup>
        <ConfigFields fields={fields} path="" errors={errors} onChange={change} />
      </FieldGroup>
      <FieldError errors={unplaced(fields, "", errors)} />
      <p className="text-sm text-muted-foreground">
        Saving rewrites package.yaml from these values, so the comments in it are not kept.
      </p>
      <div className="flex items-center gap-3">
        <Button
          disabled={saving || JSON.stringify(fields) === JSON.stringify(opened.fields)}
          onClick={() => void save()}
        >
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">
            Saved. It applies once the instance is recreated.
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{lastChanged(opened.lastChange, opened.hash)}</p>
    </div>
  )
}

type Errors = Record<string, string>

/** The validator's `location` of a field, such as `review.round_limit`, in the section at `path`. */
function located(path: string, name: string): string {
  return path === "" ? name : `${path}.${name}`
}

/** Whether the validator's `location`, such as `skills.review`, is at `path` or within it. */
function belongsTo(location: string, path: string): boolean {
  return location === path || location.startsWith(`${path}.`)
}

function errorsAt(errors: Errors, path: string): Errors {
  return Object.fromEntries(
    Object.entries(errors).filter(([location]) => belongsTo(location, path)),
  )
}

/** Where `location` is within `path`: `skills.review` is `review` within `skills`. */
function within(location: string, path: string): string {
  return path === "" ? location : location.slice(path.length + 1)
}

/** The reasons within the section at `path` that none of its `fields` takes, each naming where. */
function unplaced(fields: FormField[], path: string, errors: Errors): { message: string }[] {
  return Object.entries(errors)
    .filter(([location]) => !fields.some((field) => belongsTo(location, located(path, field.name))))
    .map(([location, message]) => {
      const where = within(location, path)
      return { message: where === "" ? message : `${where}: ${message}` }
    })
}

/** The validator's reasons for the leaf `field` at `path`, each naming the item or key. */
function leafErrors(field: FormField, path: string, errors: Errors): { message: string }[] {
  return Object.entries(errors).map(([location, message]) => {
    const where = within(location, path)
    if (where === "") return { message }
    const item = Number.parseInt(where, 10)
    return {
      message: field.type === "list" ? `Item ${item + 1}: ${message}` : `${where}: ${message}`,
    }
  })
}

/** The controls for the `fields` of the section at `path`, each given the reasons within it. */
function ConfigFields({
  fields,
  path,
  errors,
  onChange,
}: {
  fields: FormField[]
  path: string
  errors: Errors
  onChange: (fields: FormField[]) => void
}) {
  return fields.map((field, index) => {
    const at = located(path, field.name)
    return (
      <ConfigInput
        key={field.name}
        field={field}
        path={at}
        errors={errorsAt(errors, at)}
        onChange={(next) =>
          onChange(fields.map((other, place) => (place === index ? next : other)))
        }
      />
    )
  })
}

/** The control for the field at `path`, by the type the package declared for it. */
function ConfigInput({
  field,
  path,
  errors,
  onChange,
}: {
  field: FormField
  path: string
  errors: Errors
  onChange: (field: FormField) => void
}) {
  const id = `package-config-${path}`
  const description = field.description !== undefined && (
    <FieldDescription>{field.description}</FieldDescription>
  )
  if (field.type === "section" || field.type === "optional section") {
    // An absent section shows no fields, so the reasons within it all show on the section.
    const shown = field.type === "section" || field.present ? field.fields : []
    return (
      <FieldSet>
        {field.type === "section" ? (
          <FieldLegend>{field.label}</FieldLegend>
        ) : (
          <FieldLegend>
            <span className="flex items-center gap-2">
              <Switch
                id={id}
                checked={field.present}
                onCheckedChange={(present) => onChange({ ...field, present })}
              />
              <FieldLabel htmlFor={id}>{field.label}</FieldLabel>
            </span>
          </FieldLegend>
        )}
        {description}
        {shown.length > 0 && (
          <FieldGroup>
            <ConfigFields
              fields={shown}
              path={path}
              errors={errors}
              onChange={(fields) => onChange({ ...field, fields })}
            />
          </FieldGroup>
        )}
        <FieldError errors={unplaced(shown, path, errors)} />
      </FieldSet>
    )
  }
  const messages = leafErrors(field, path, errors)
  const invalid = messages.length > 0 || undefined
  switch (field.type) {
    case "boolean":
      return (
        <Field orientation="horizontal" data-invalid={invalid}>
          <Switch
            id={id}
            checked={field.value}
            aria-invalid={invalid}
            onCheckedChange={(checked) => onChange({ ...field, value: checked })}
          />
          <FieldContent>
            <FieldLabel htmlFor={id}>{field.label}</FieldLabel>
            {description}
            <FieldError errors={messages} />
          </FieldContent>
        </Field>
      )
    case "map":
      return (
        <FieldSet data-invalid={invalid}>
          <FieldLegend variant="label">{field.label}</FieldLegend>
          {description}
          <MapRows
            label={field.label}
            rows={field.value}
            onChange={(value) => onChange({ ...field, value })}
          />
          <FieldError errors={messages} />
        </FieldSet>
      )
    case "unsupported":
      return (
        <Field>
          <FieldLabel>{field.label}</FieldLabel>
          <FieldDescription>
            The form cannot edit this field, so a save keeps its value. Edit it in package.yaml.
          </FieldDescription>
          <FieldError errors={messages} />
        </Field>
      )
  }
  let control
  switch (field.type) {
    case "enum":
      control = (
        <Select
          value={field.value === "" ? null : field.value}
          onValueChange={(picked) =>
            onChange({ ...field, value: typeof picked === "string" ? picked : "" })
          }
        >
          <SelectTrigger id={id} aria-invalid={invalid}>
            <SelectValue placeholder="Pick one" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {field.choices.map((choice) => (
                <SelectItem key={choice} value={choice}>
                  {choice}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      )
      break
    case "list":
      control = (
        <Textarea
          id={id}
          value={field.value}
          aria-invalid={invalid}
          onChange={(event) => onChange({ ...field, value: event.target.value })}
        />
      )
      break
    default:
      control = (
        <Input
          id={id}
          type={field.type === "integer" ? "number" : "text"}
          step={field.type === "integer" ? 1 : undefined}
          autoComplete="off"
          value={field.value}
          aria-invalid={invalid}
          onChange={(event) => onChange({ ...field, value: event.target.value })}
        />
      )
  }
  return (
    <Field data-invalid={invalid}>
      <FieldLabel htmlFor={id}>{field.label}</FieldLabel>
      {control}
      {description}
      {field.type === "list" && <FieldDescription>One per line.</FieldDescription>}
      <FieldError errors={messages} />
    </Field>
  )
}

/** A map from string to string, one row per key, each removable, and a button to add one. */
function MapRows({
  label,
  rows,
  onChange,
}: {
  label: string
  rows: [string, string][]
  onChange: (rows: [string, string][]) => void
}) {
  const edit = (index: number, row: [string, string]) =>
    onChange(rows.map((other, at) => (at === index ? row : other)))
  return (
    <div className="flex flex-col gap-2">
      {rows.map(([key, value], index) => (
        // A row is its position: its key is what the user is editing.
        <div key={index} className="flex gap-2">
          <Input
            aria-label={`${label} ${index + 1} name`}
            placeholder="Name"
            value={key}
            onChange={(event) => edit(index, [event.target.value, value])}
          />
          <Input
            aria-label={`${label} ${index + 1} value`}
            placeholder="Value"
            value={value}
            onChange={(event) => edit(index, [key, event.target.value])}
          />
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Remove ${key === "" ? `row ${index + 1}` : key}`}
            onClick={() => onChange(rows.filter((_, at) => at !== index))}
          >
            <XIcon />
          </Button>
        </div>
      ))}
      <div>
        <Button
          variant="outline"
          aria-label={`Add to ${label}`}
          onClick={() => onChange([...rows, ["", ""]])}
        >
          Add
        </Button>
      </div>
    </div>
  )
}
