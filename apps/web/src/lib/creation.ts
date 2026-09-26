import { CallError } from "@kinby/contract"
import type {
  Avatar,
  Client,
  Clock,
  InstanceCreateCommand,
  OperationStep,
  PackageSelection,
  SetupField,
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
const MODEL_FIELD = "model"

/** What the user named the instance and how it is drawn. */
export interface Identity {
  name: string
  avatar: Avatar
}

/** The create command for a prepared selection, with each field's value sent by its kind. */
export function createCommand(
  selection: PackageSelection | null,
  fields: SetupField[],
  values: Record<string, string>,
  { name, avatar }: Identity,
): InstanceCreateCommand {
  const config: Record<string, string> = {}
  const secrets: Record<string, string> = {}
  for (const field of fields) {
    if (field.name === MODEL_FIELD) continue
    const sent = field.kind === "secret" ? secrets : config
    sent[field.name] = values[field.name] ?? ""
  }
  // The name is both the instance's manifest id and the persona it answers as.
  const trimmed = name.trim()
  return {
    manifest_id: trimmed,
    persona_name: trimmed,
    model: values[MODEL_FIELD] ?? "",
    package: selection,
    config,
    secrets,
    avatar,
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
