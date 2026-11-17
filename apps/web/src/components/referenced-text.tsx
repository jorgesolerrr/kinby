import { parts, type Reference } from "@/lib/references"

/** `text` with each skill reference in it marked apart from the typed text. */
export function ReferencedText({ text, references }: { text: string; references: Reference[] }) {
  return parts(text, references).map((part, index) =>
    part.kind === "text" ? (
      part.text
    ) : (
      <mark key={index} className="rounded-sm bg-primary/15 text-inherit">
        /{part.name}
      </mark>
    ),
  )
}
