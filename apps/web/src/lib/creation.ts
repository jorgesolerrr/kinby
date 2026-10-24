import { CallError } from "@kinby/contract"
import type {
  Avatar,
  Client,
  Clock,
  DeclaredSetup,
  FactoryRole,
  InstanceCreateCommand,
  OperationStep,
  SetupField,
  SetupValue,
} from "@kinby/contract"

import { finished, lost, pace, reason, retried } from "@/lib/operation"

export type Creation =
  | { state: "creating"; steps: OperationStep[] }
  | { state: "invalid"; fields: Record<string, string> }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "created"; steps: OperationStep[]; instanceId: string }

export type Starting =
  | { state: "starting"; steps: OperationStep[] }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "started" }

// Without its answer the client has no instance ID to ask about, and asking again would create
// a second instance.
const CREATE_LOST =
  "The connection dropped before the hub answered. If the hub got the request, " +
  "the instance shows in the sidebar once it is published."

/** The built-in field whose value travels as the command's own model, not in its config. */
export const MODEL_FIELD = "model"

/** The built-in secret field the instance holds under its provider's variable. */
const API_KEY_FIELD = "api_key"

/** What a field's control holds: the text typed or picked, or whether a switch is on. */
export type FieldInput = string | boolean

/**
 * What a field's control starts from: the field's default, or nothing yet.
 * A boolean with no default stays unset, so an untouched switch is not a false.
 */
export function initialInput(field: SetupField): FieldInput | undefined {
  if (field.type === "boolean") {
    return typeof field.default === "boolean" ? field.default : undefined
  }
  return field.default == null ? "" : String(field.default)
}

/** The value sent for a control's input. Text that is not a whole number goes as typed, and the hub marks it. */
export function setupValue(field: SetupField, input: FieldInput): SetupValue {
  if (field.type !== "integer" || typeof input !== "string" || input.trim() === "") return input
  const number = Number(input)
  return Number.isInteger(number) ? number : input
}

/**
 * Where a secret field's value comes from: the hub secret the field links, when one is set, or
 * else the text typed, saved to the hub under `save` when that is set.
 */
export interface SecretSource {
  hubSecret?: string
  save?: string
}

/** The variable a secret field's value is held under. The API key's is its provider's. */
export function heldVariable(field: SetupField, model: string): string {
  if (field.name !== API_KEY_FIELD) return field.name
  const [provider = ""] = model.split(":")
  return `${provider.toUpperCase()}_API_KEY`
}

/** The source the user chose, else the hub secret named after the variable, else typing. */
export function secretSource(
  chosen: SecretSource | undefined,
  variable: string,
  kept: string[],
): SecretSource {
  return chosen ?? (kept.includes(variable) ? { hubSecret: variable } : {})
}

/** The field the onboarding asks for in its own step, starting from kinby's or the role's. */
export const PROMPT_FIELD = "behavior_prompt"

/** What the user named the instance and how it is drawn. */
export interface Identity {
  name: string
  avatar: Avatar
}

/** What the instance picks: a factory role, or none for a vanilla instance, and its skills. */
export interface Picks {
  role: FactoryRole | undefined
  skills: string[]
}

/** A factory role the library offers, with what an instance that picks it asks for. */
export interface RoleOffer {
  role: FactoryRole
  setup: DeclaredSetup
  suggestedPicks: string[]
}

/** Each role of each factory in the library. */
export async function offeredRoles(caller: Pick<Client, "call">): Promise<RoleOffer[]> {
  const { factories } = await caller.call("factory.list", {})
  const described = await Promise.all(
    factories.map(async ({ name }) => ({
      factory: name,
      ...(await caller.call("factory.describe", { name })),
    })),
  )
  return described.flatMap(({ factory, instances, suggested_picks }) =>
    Object.entries(instances).map(([name, setup]) => ({
      role: { factory, name },
      setup,
      suggestedPicks: suggested_picks[name] ?? [],
    })),
  )
}

/** The name an instance that picks *role* starts with, as the hub would give it. */
export function proposedName(role: FactoryRole | undefined): string {
  return role === undefined ? "" : `${role.factory}-${role.name}`
}

/**
 * The create command for the prepared image or the role, with each field's value by its kind,
 * and each secret field's from its source.
 */
export function createCommand(
  fields: SetupField[],
  inputs: Record<string, FieldInput>,
  { name, avatar }: Identity,
  { role, skills }: Picks,
  sources: Record<string, SecretSource> = {},
): InstanceCreateCommand {
  const config: Record<string, SetupValue> = {}
  const secrets: Record<string, string> = {}
  const hubSecrets: Record<string, string> = {}
  const saveToHub: Record<string, string> = {}
  let model = ""
  for (const field of fields) {
    const source = sources[field.name] ?? {}
    if (field.kind === "secret" && source.hubSecret !== undefined) {
      hubSecrets[field.name] = source.hubSecret
      continue
    }
    const input = inputs[field.name] ?? initialInput(field)
    if (input === undefined) continue
    const value = setupValue(field, input)
    if (field.name === MODEL_FIELD) model = String(value)
    else if (field.kind === "secret") {
      const text = String(value)
      if (text.trim() === "") continue
      secrets[field.name] = text
      if (source.save !== undefined) saveToHub[field.name] = source.save
    } else config[field.name] = value
  }
  // The name is both the instance's manifest id and the persona it answers as.
  const trimmed = name.trim()
  return {
    manifest_id: trimmed,
    persona_name: trimmed,
    factory_role: role,
    model,
    config,
    secrets,
    hub_secrets: hubSecrets,
    save_to_hub: saveToHub,
    avatar,
    picks: skills,
  }
}

const NOT_STARTED =
  "The connection dropped before the hub answered, and the instance is not running. " +
  "Start it again from its page."

/**
 * Create an instance and report each step the hub reaches, or the fields it refused.
 * The returned function stops following it; the hub carries on.
 */
export function followCreation(
  caller: Pick<Client, "call">,
  command: InstanceCreateCommand,
  report: (creation: Creation) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (creation: Creation) => {
    if (!pacing.stopped) report(creation)
  }

  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const accepted = await caller.call("instance.create", command)
      const operation = await finished(caller, accepted.operation_id, pacing, (reached) => {
        steps = reached
        emit({ state: "creating", steps })
      })
      steps = operation.steps ?? []
      if (operation.state === "failed") {
        return emit({ state: "failed", steps, detail: operation.detail })
      }
      emit({ state: "created", steps, instanceId: accepted.instance_id })
    } catch (error) {
      if (error instanceof CallError && error.code === "INVALID_SETUP") {
        return emit({ state: "invalid", fields: error.fields })
      }
      emit({ state: "failed", steps, detail: lost(error) ? CREATE_LOST : reason(error) })
    }
  }

  void follow()
  return pacing.stop
}

/**
 * Start an instance and report each step until it runs. A start whose answer was lost is found
 * again through the instance's active operation.
 */
export function followStart(
  caller: Pick<Client, "call">,
  instanceId: string,
  report: (starting: Starting) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (starting: Starting) => {
    if (!pacing.stopped) report(starting)
  }

  const operationOfStart = async (): Promise<string | null> => {
    try {
      return (await caller.call("instance.start", { instance_id: instanceId })).operation_id
    } catch (error) {
      if (!lost(error)) throw error
      const status = await retried(
        () => caller.call("instance.status", { instance_id: instanceId }),
        pacing,
      )
      if (status.active_operation_id != null) return status.active_operation_id
      if (status.process === "running") return null
      throw new Error(NOT_STARTED)
    }
  }

  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const operationId = await operationOfStart()
      if (operationId === null) return emit({ state: "started" })
      const operation = await finished(caller, operationId, pacing, (reached) => {
        steps = reached
        emit({ state: "starting", steps })
      })
      if (operation.state === "failed") {
        return emit({ state: "failed", steps: operation.steps ?? [], detail: operation.detail })
      }
      emit({ state: "started" })
    } catch (error) {
      emit({ state: "failed", steps, detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}
