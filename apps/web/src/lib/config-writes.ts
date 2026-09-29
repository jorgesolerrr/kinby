import { CallError } from "@kinby/contract"

/**
 * A write the instance refused: the file changed since it was read, or some values are invalid,
 * with the reason for each by its field.
 */
export type Refused = { state: "stale" } | { state: "invalid"; fields: Record<string, string> }

/** How the instance refused a config write, or `error` again when it did not refuse it. */
export function refusal(error: unknown): Refused {
  if (error instanceof CallError && error.code === "STALE") return { state: "stale" }
  if (error instanceof CallError && error.code === "INVALID_ARGUMENT") {
    return { state: "invalid", fields: error.fields }
  }
  throw error
}
