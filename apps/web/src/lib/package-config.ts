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
 * and a list is one item per line, so the package's validator judges what was typed. A field of a
 * type the form does not offer keeps its value and is sent back unchanged.
 */
export type FormField = Described &
  (
    | { type: "string"; value: string }
    | { type: "integer"; value: string }
    | { type: "boolean"; value: boolean }
    | { type: "enum"; choices: string[]; value: string }
    | { type: "list"; value: string }
    | { type: "map"; value: [string, string][] }
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
    config = await caller.call("package.config.set", {
      values: Object.fromEntries(fields.map((field) => [field.name, sent(field)])),
      hash,
    })
  } catch (error) {
    return refusal(error)
  }
  return { state: "saved", opened: opened(config, await latestChange(caller, PACKAGE_CONFIG_FILE)) }
}

function opened(
  { schema, values, hash }: PackageConfigResult,
  lastChange: ConfigChange | undefined,
): OpenedPackageConfig {
  const definitions = object(schema.$defs)
  const fields = Object.entries(object(schema.properties)).map(([name, declared]) => {
    const property = resolved(declared, definitions)
    const own = object(declared)
    const described: Described = {
      name,
      label: text(own.title) ?? text(property.title) ?? name,
      description: text(own.description) ?? text(property.description),
    }
    return formField(
      described,
      property,
      name in values ? values[name] : property.default,
      definitions,
    )
  })
  return { fields, hash, lastChange }
}

function formField(
  described: Described,
  property: JsonObject,
  value: unknown,
  definitions: JsonObject,
): FormField {
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

/** A field's value as the package's config model takes it. */
function sent(field: FormField): unknown {
  switch (field.type) {
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
