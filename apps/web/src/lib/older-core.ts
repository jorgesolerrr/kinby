import type { InstanceSummary } from "@kinby/contract"
import { createContext } from "react"

/**
 * Opens "Core version" when the instance runs a revision behind the hub's, so a method it
 * does not know means its core is older than the panel. Undefined otherwise.
 */
export const OlderCore = createContext<(() => void) | undefined>(undefined)

/** Whether the instance runs a revision behind the hub's. */
export function behind(instance: InstanceSummary): boolean {
  return instance.notices.some((notice) => notice.code === "revision_behind")
}
