// The drafts live as long as the page, so a composer gets its text back when its screen opens again.
const drafts = new Map<string, string>()

/** The unsent text kept under `key`, or an empty one. */
export function draft(key: string): string {
  return drafts.get(key) ?? ""
}

/** Keep `text` under `key`, and forget the draft once `text` is empty. */
export function keepDraft(key: string, text: string): void {
  if (text === "") drafts.delete(key)
  else drafts.set(key, text)
}

/** Forget every draft, for a test that opens the app afresh. */
export function forgetDrafts(): void {
  drafts.clear()
}
