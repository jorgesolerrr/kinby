import type { Reference } from "@/lib/references"

/** An unsent message: its text and the skill references picked into it. */
export interface Draft {
  text: string
  references: Reference[]
}

export const EMPTY_DRAFT: Draft = { text: "", references: [] }

// The drafts live as long as the page, so a composer gets its text back when its screen opens again.
const drafts = new Map<string, Draft>()

/** The unsent message kept under `key`, or an empty one. */
export function draft(key: string): Draft {
  return drafts.get(key) ?? EMPTY_DRAFT
}

/** Keep `kept` under `key`, and forget the draft once its text is empty. */
export function keepDraft(key: string, kept: Draft): void {
  if (kept.text === "") drafts.delete(key)
  else drafts.set(key, kept)
}

/** Forget every draft, for a test that opens the app afresh. */
export function forgetDrafts(): void {
  drafts.clear()
}
