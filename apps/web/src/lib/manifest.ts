import { CallError } from "@kinby/contract"
import type {
  ConfigChange,
  InstanceClient,
  ManifestBudgets,
  ManifestResult,
  ManifestValues,
  NewModelPrice,
} from "@kinby/contract"

import { latestChange } from "@/lib/config-changes"

/** The file the manifest is kept in, as the config changes name it. */
export const MANIFEST_FILE = "kinby.toml"

/** A model name the instance can price: `provider:model`, with no spaces. */
export const MODEL_NAME = /^[^:\s]+:[^:\s]+$/

/** The manifest as the instance has it, and the latest change to its file. */
export interface OpenedManifest {
  manifest: ManifestResult
  lastChange: ConfigChange | undefined
}

/**
 * A save the instance took, one it refused because the file changed since it was read, or one
 * it refused for the values it names.
 */
export type SavedManifest =
  | { state: "saved"; opened: OpenedManifest }
  | { state: "stale" }
  | { state: "invalid"; fields: Record<string, string> }

type Caller = Pick<InstanceClient, "call">

export async function openManifest(caller: Caller): Promise<OpenedManifest> {
  const [manifest, lastChange] = await Promise.all([
    caller.call("manifest.get", {}),
    latestChange(caller, MANIFEST_FILE),
  ])
  return { manifest, lastChange }
}

/** Write `values` and the new `prices` over the manifest read with `hash`. */
export async function saveManifest(
  caller: Caller,
  values: ManifestValues,
  prices: Record<string, NewModelPrice>,
  hash: string,
): Promise<SavedManifest> {
  let manifest: ManifestResult
  try {
    manifest = await caller.call("manifest.set", { values, prices, hash })
  } catch (error) {
    if (error instanceof CallError && error.code === "STALE") return { state: "stale" }
    if (error instanceof CallError && error.code === "INVALID_ARGUMENT") {
      return { state: "invalid", fields: error.fields }
    }
    throw error
  }
  return {
    state: "saved",
    opened: { manifest, lastChange: await latestChange(caller, MANIFEST_FILE) },
  }
}

type NumberText = Record<keyof ManifestBudgets | "bash_timeout_seconds", string>

/** The form's copy of the values. The numbers stay as typed, so a blank budget can mean no limit. */
export interface ManifestDraft {
  values: ManifestValues
  numbers: NumberText
}

export function draftOf(values: ManifestValues): ManifestDraft {
  const text = (value: number | null) => (value === null ? "" : String(value))
  return {
    values,
    numbers: {
      steps: text(values.budgets.steps),
      tokens: text(values.budgets.tokens),
      seconds: text(values.budgets.seconds),
      usd_per_day: text(values.budgets.usd_per_day),
      bash_timeout_seconds: text(values.tools.bash_timeout_seconds),
    },
  }
}

/** The values a draft sends, or undefined while a number in it is not one. */
export function valuesOf(draft: ManifestDraft): ManifestValues | undefined {
  const budget = (text: string) => (text.trim() === "" ? null : Number(text))
  const budgets: ManifestBudgets = {
    steps: budget(draft.numbers.steps),
    tokens: budget(draft.numbers.tokens),
    seconds: budget(draft.numbers.seconds),
    usd_per_day: budget(draft.numbers.usd_per_day),
  }
  const timeout = draft.numbers.bash_timeout_seconds
  if (timeout.trim() === "") return undefined
  if (Object.values(budgets).some((value) => Number.isNaN(value))) return undefined
  if (Number.isNaN(Number(timeout))) return undefined
  return {
    ...draft.values,
    budgets,
    tools: { ...draft.values.tools, bash_timeout_seconds: Number(timeout) },
  }
}
