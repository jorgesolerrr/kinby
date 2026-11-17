/** A skill reference: the `/name` token a pick from the composer's menu put at `at` in a message. */
export interface Reference {
  name: string
  at: number
}

/** A message cut into its typed text and its references, in order. */
export type Part = { kind: "text"; text: string } | { kind: "reference"; name: string }

/**
 * The references of `before` that stand unchanged in `after`, the text an edit left with the caret
 * at `caret`, moved by what the edit inserted or removed ahead of them. An edit that touches a
 * token drops it.
 */
export function carried(
  references: Reference[],
  before: string,
  after: string,
  caret: number,
): Reference[] {
  // The edit ends at the caret, so the text the two share after it is all that can stand unchanged.
  let suffix = 0
  const shared = Math.min(before.length, after.length, after.length - caret)
  while (suffix < shared && before.at(-1 - suffix) === after.at(-1 - suffix)) suffix += 1
  let prefix = 0
  const ahead = Math.min(before.length, after.length) - suffix
  while (prefix < ahead && before[prefix] === after[prefix]) prefix += 1
  const shift = after.length - before.length
  return references.flatMap((reference) => {
    const end = reference.at + reference.name.length + 1
    const at =
      end <= prefix
        ? reference.at
        : reference.at >= before.length - suffix
          ? reference.at + shift
          : undefined
    return at !== undefined && standsAt(after, reference.name, at)
      ? [{ name: reference.name, at }]
      : []
  })
}

/**
 * The word the caret ends when it starts with a `/` at the start of `text` or after whitespace:
 * where its slash is, and what follows it. A reference's own token is no such word.
 */
export function slashWord(
  text: string,
  caret: number,
  references: Reference[],
): { at: number; query: string } | undefined {
  const word = /(?:^|\s)\/(\S*)$/.exec(text.slice(0, caret))
  if (word === null || !bounds(text[caret])) return undefined
  const at = caret - word[1].length - 1
  return references.some((reference) => reference.at === at) ? undefined : { at, query: word[1] }
}

/** Where each `/name` token of `names` stands in `text`, as a message's references recorded them. */
export function referencesIn(text: string, names: string[]): Reference[] {
  return names.flatMap((name) =>
    [...text.matchAll(/\/\S+/g)].flatMap((token) =>
      standsAt(text, name, token.index) ? [{ name, at: token.index }] : [],
    ),
  )
}

/** `text` cut at its references. */
export function parts(text: string, references: Reference[]): Part[] {
  const cut: Part[] = []
  let from = 0
  for (const { name, at } of references.toSorted((a, b) => a.at - b.at)) {
    if (at > from) cut.push({ kind: "text", text: text.slice(from, at) })
    cut.push({ kind: "reference", name })
    from = at + name.length + 1
  }
  if (from < text.length) cut.push({ kind: "text", text: text.slice(from) })
  return cut
}

/** Whether `/name` stands at `at` in `text` as a word of its own. */
function standsAt(text: string, name: string, at: number): boolean {
  const end = at + name.length + 1
  return text.slice(at, end) === `/${name}` && bounds(text[at - 1]) && bounds(text[end])
}

/** Whether `char`, beside a token, leaves it a word of its own: the text's edge or whitespace. */
function bounds(char: string | undefined): boolean {
  return char === undefined || /\s/.test(char)
}
