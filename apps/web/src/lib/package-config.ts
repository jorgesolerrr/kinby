import { CallError } from "@kinby/contract"
import type { ConfigChange, InstanceClient, PackageConfigResult } from "@kinby/contract"

import { latestChange, readWithChange } from "@/lib/config-changes"
import { type Refused, refusal } from "@/lib/config-writes"

export const PACKAGE_CONFIG_FILE = "package.yaml"

interface Described {
  name: string
  label: string
  description: string | undefined
}

/** A number field's bounds as its schema declares them, each absent when not declared. */
interface Bounds {
  minimum: number | undefined
  maximum: number | undefined
  exclusiveMinimum: number | undefined
  exclusiveMaximum: number | undefined
}

/**
 * One field of the package's config with its value as the form edits it. A number is its text and
 * a list is one item per line, so the package's validator judges what was typed. A section
 * holds the fields of a nested model, and an optional section is sent as null when not present. A
 * field of a type the form does not offer keeps its value and is sent back unchanged.
 */
export type FormField = Described &
  (
    | { type: "string"; value: string }
    | { type: "number"; integer: boolean; bounds: Bounds; value: string }
    | { type: "boolean"; value: boolean }
    | { type: "enum"; choices: string[]; value: string }
    | { type: "list"; value: string }
    | { type: "map"; value: [string, string][] }
    | { type: "section"; fields: FormField[] }
    | { type: "optional section"; fields: FormField[]; present: boolean }
    | { type: "unsupported"; value: unknown }
  )

/** The package config as the instance has it, and the latest change to package.yaml. */
export interface OpenedPackageConfig {
  fields: FormField[]
  hash: string
  lastChange: ConfigChange | undefined
}

export type SavedPackageConfig = { state: "saved"; opened: OpenedPackageConfig } | Refused

type Caller = Pick<InstanceClient, "call">
type JsonObject = Record<string, unknown>

/** The package config, or null when the instance runs no package or its package declares none. */
export async function openPackageConfig(caller: Caller): Promise<OpenedPackageConfig | null> {
  try {
    return opened(
      ...(await readWithChange(caller, PACKAGE_CONFIG_FILE, () =>
        caller.call("package.config.get", {}),
      )),
    )
  } catch (error) {
    if (error instanceof CallError && error.code === "NOT_FOUND") return null
    throw error
  }
}

/** Write `fields` over the package config read with `hash`. */
export async function savePackageConfig(
  caller: Caller,
  fields: FormField[],
  hash: string,
): Promise<SavedPackageConfig> {
  let config: PackageConfigResult
  try {
    config = await caller.call("package.config.set", { values: sentValues(fields), hash })
  } catch (error) {
    return refusal(error)
  }
  return { state: "saved", opened: opened(config, await latestChange(caller, PACKAGE_CONFIG_FILE)) }
}

/** The validator's `location` of a field, such as `review.round_limit`, in the section at `path`. */
export function located(path: string, name: string): string {
  return path === "" ? name : `${path}.${name}`
}

/**
 * The reasons the form refuses to save, by the location of their field. A number out of its bounds
 * is one. The package's validator still judges every value that passes.
 */
export function refusals(fields: FormField[], path = ""): Record<string, string> {
  return Object.fromEntries(
    fields.flatMap((field): [string, string][] => {
      const at = located(path, field.name)
      if (field.type === "section" || (field.type === "optional section" && field.present)) {
        return Object.entries(refusals(field.fields, at))
      }
      if (field.type !== "number") return []
      const reason = outOfBounds(typed(field.value), field.bounds)
      return reason === undefined ? [] : [[at, reason]]
    }),
  )
}

function outOfBounds(value: number | undefined, bounds: Bounds): string | undefined {
  if (value === undefined) return undefined
  const { minimum, maximum, exclusiveMinimum, exclusiveMaximum } = bounds
  if (minimum !== undefined && value < minimum) return `Must be at least ${minimum}.`
  if (maximum !== undefined && value > maximum) return `Must be at most ${maximum}.`
  if (exclusiveMinimum !== undefined && value <= exclusiveMinimum) {
    return `Must be greater than ${exclusiveMinimum}.`
  }
  if (exclusiveMaximum !== undefined && value >= exclusiveMaximum) {
    return `Must be less than ${exclusiveMaximum}.`
  }
  return undefined
}

function opened(
  { schema, values, hash }: PackageConfigResult,
  lastChange: ConfigChange | undefined,
): OpenedPackageConfig {
  return { fields: formFields(schema, values, object(schema.$defs), []), hash, lastChange }
}

/**
 * The fields of a model's schema, each holding its value in `values` or else its default.
 * `within` holds the definitions of the sections around these fields.
 */
function formFields(
  model: JsonObject,
  values: unknown,
  definitions: JsonObject,
  within: string[],
): FormField[] {
  const given = object(values)
  return Object.entries(object(model.properties)).map(([name, declared]) => {
    const own = object(declared)
    return formField(name, own, name in given ? given[name] : own.default, definitions, within)
  })
}

function formField(
  name: string,
  declared: JsonObject,
  value: unknown,
  definitions: JsonObject,
  within: string[],
): FormField {
  const optional = optionalModel(declared, definitions)
  const property = optional ?? resolved(declared, definitions)
  // A referenced definition's title names its type, such as ReasoningEffort, so a field without a
  // title of its own takes the one pydantic gives a field.
  const described: Described = {
    name,
    label: text(declared.title) ?? titled(name),
    description: text(declared.description) ?? text(property.description),
  }
  // A model that holds itself, such as a node with an optional child node, would nest without end:
  // the form stops at its second appearance and keeps that field's value as it is.
  const reference = definitionOf(declared)
  if (reference !== undefined && within.includes(reference)) {
    return { ...described, type: "unsupported", value }
  }
  const nested = reference === undefined ? within : [...within, reference]
  if (optional !== undefined) {
    return {
      ...described,
      type: "optional section",
      fields: formFields(optional, value, definitions, nested),
      present: value !== null && value !== undefined,
    }
  }
  if (isModel(property)) {
    return {
      ...described,
      type: "section",
      fields: formFields(property, value, definitions, nested),
    }
  }
  if (Array.isArray(property.enum)) {
    const choices = property.enum.filter((choice) => typeof choice === "string")
    return choices.length === property.enum.length
      ? { ...described, type: "enum", choices, value: text(value) ?? "" }
      : { ...described, type: "unsupported", value }
  }
  switch (property.type) {
    case "string":
      return { ...described, type: "string", value: text(value) ?? "" }
    case "number":
    case "integer":
      return {
        ...described,
        type: "number",
        integer: property.type === "integer",
        bounds: {
          minimum: numeric(property.minimum),
          maximum: numeric(property.maximum),
          exclusiveMinimum: numeric(property.exclusiveMinimum),
          exclusiveMaximum: numeric(property.exclusiveMaximum),
        },
        value: typeof value === "number" ? String(value) : "",
      }
    case "boolean":
      return { ...described, type: "boolean", value: value === true }
    case "array":
      if (!plainString(resolved(property.items, definitions))) break
      return {
        ...described,
        type: "list",
        value: Array.isArray(value)
          ? value.filter((item) => typeof item === "string").join("\n")
          : "",
      }
    case "object":
      if (!plainString(property.additionalProperties)) break
      return {
        ...described,
        type: "map",
        value: Object.entries(object(value)).flatMap(([key, item]) =>
          typeof item === "string" ? [[key, item] satisfies [string, string]] : [],
        ),
      }
  }
  return { ...described, type: "unsupported", value }
}

function sentValues(fields: FormField[]): Record<string, unknown> {
  return Object.fromEntries(fields.map((field) => [field.name, sent(field)]))
}

/** A field's value as the package's config model takes it. */
function sent(field: FormField): unknown {
  switch (field.type) {
    case "section":
      return sentValues(field.fields)
    case "optional section":
      return field.present ? sentValues(field.fields) : null
    case "number":
      return typed(field.value) ?? field.value
    case "list":
      return field.value.split("\n").filter((line) => line.trim() !== "")
    case "map":
      return Object.fromEntries(field.value.filter(([key]) => key !== ""))
    default:
      return field.value
  }
}

/** A property's schema, or the definition it references, as a StrEnum field's does. */
function resolved(property: unknown, definitions: JsonObject): JsonObject {
  const own = object(property)
  const reference = text(own.$ref)
  return reference === undefined ? own : object(definitions[reference.replace("#/$defs/", "")])
}

/** The definition a field references, itself or through a branch of its `anyOf`. */
function definitionOf(declared: JsonObject): string | undefined {
  const branches: unknown[] = Array.isArray(declared.anyOf) ? declared.anyOf : [declared]
  return branches.map((branch) => text(object(branch).$ref)).find((ref) => ref !== undefined)
}

/** Whether `property` is a nested model, whose fields the form shows as a section. */
function isModel(property: JsonObject): boolean {
  return property.type === "object" && property.properties !== undefined
}

/** The model a field of type `Model | None` holds when present, as `anyOf` of the model and null. */
function optionalModel(declared: JsonObject, definitions: JsonObject): JsonObject | undefined {
  if (!Array.isArray(declared.anyOf) || declared.anyOf.length !== 2) return undefined
  const branches = declared.anyOf.map((branch) => resolved(branch, definitions))
  const model = branches.find(isModel)
  return model !== undefined && branches.some((branch) => branch.type === "null")
    ? model
    : undefined
}

/** A field's name as pydantic titles it: `round_limit` is "Round Limit". */
function titled(name: string): string {
  return name
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
}

function plainString(schema: unknown): boolean {
  const property = object(schema)
  return property.type === "string" && property.enum === undefined
}

function object(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value))
    : {}
}

function numeric(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined
}

/** The number `value` reads as, or undefined when it reads as none. */
function typed(value: string): number | undefined {
  const number = Number(value)
  return value.trim() !== "" && Number.isFinite(number) ? number : undefined
}

function text(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}
