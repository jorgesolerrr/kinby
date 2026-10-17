import type { TurnBlock } from "@/lib/timeline"

/** What started a turn or a thread, named the same in the thread list and the transcript. */
export function originLabel(origin: TurnBlock["startedBy"]): string {
  switch (origin.kind) {
    case "user":
      return "You"
    case "routine":
      return `Routine ${origin.name}`
    case "factory-run":
      return `Factory ${origin.factory} · ${origin.step}`
    case "home-instance":
      return "Home instance"
  }
}
