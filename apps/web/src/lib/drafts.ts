import type { Reference } from "@/lib/references"
import type { Upload } from "@/lib/uploads"

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

// A draft's files settle while its composer is closed, so their readers listen for a change.
const uploads = new Map<string, Upload[]>()
const listeners = new Set<() => void>()
const NO_UPLOADS: Upload[] = []

/** The files added to the unsent message under `key`. */
export function draftUploads(key: string): Upload[] {
  return uploads.get(key) ?? NO_UPLOADS
}

/** Change the files under `key`, and tell every reader. */
export function keepUploads(key: string, change: (current: Upload[]) => Upload[]): void {
  const kept = change(draftUploads(key))
  if (kept.length === 0) uploads.delete(key)
  else uploads.set(key, kept)
  for (const listener of listeners) listener()
}

/** Call `listener` on each change of any draft's files, until the returned function is called. */
export function watchUploads(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Forget every draft, for a test that opens the app afresh. */
export function forgetDrafts(): void {
  drafts.clear()
  uploads.clear()
}
