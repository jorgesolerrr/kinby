import { CallError } from "@kinby/contract"
import type { ConfigChange, InstanceClient, PackageConfigResult } from "@kinby/contract"

import { latestChange } from "@/lib/config-changes"
import { type Refused, refusal } from "@/lib/config-writes"

export const PACKAGE_CONFIG_FILE = "package.yaml"

interface Described {
  name: string
  label: string
  description: string | undefined
}

/**
 * One field of the package's config with its value as the form edits it. An integer is its text
 * and a list is one item per line, so the package's validator judges what was typed. A section
 * holds the fields of a nested model, and an optional section is sent as null when not present. A
 * field of a type the form does not offer keeps its value and is sent back unchanged.
 */
export type FormField = Described &
  (
    | { type: "string"; value: string }
    | { type: "integer"; value: string }
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
  let config: PackageConfigResult
  try {
    config = await caller.call("package.config.get", {})
  } catch (error) {
    if (error instanceof CallError && error.code === "NOT_FOUND") return null
    throw error
  }
  return opened(config, await latestChange(caller, PACKAGE_CONFIG_FILE))
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

function opened(
  { schema, values, hash }: PackageConfigResult,
  lastChange: ConfigChange | undefined,
): OpenedPackageConfig {
  return { fields: formFields(schema, values, object(schema.$defs)), hash, lastChange }
}

/** The fields of a model's schema, each holding its value in `values` or else its default. */
function formFields(model: JsonObject, values: unknown, definitions: JsonObject): FormField[] {
  const given = object(values)
  return Object.entries(object(model.properties)).map(([name, declared]) => {
    const own = object(declared)
    return formField(name, own, name in given ? given[name] : own.default, definitions)
  })
}

function formField(
  name: string,
  declared: JsonObject,
  value: unknown,
  definitions: JsonObject,
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
  if (optional !== undefined) {
    return {
      ...described,
      type: "optional section",
      fields: formFields(optional, value, definitions),
      present: value !== null && value !== undefined,
    }
  }
  if (isModel(property)) {
    return { ...described, type: "section", fields: formFields(property, value, definitions) }
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
    case "integer":
      return {
        ...described,
        type: "integer",
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
    case "integer":
      return /^-?\d+$/.test(field.value.trim()) ? Number(field.value) : field.value
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

function text(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}
