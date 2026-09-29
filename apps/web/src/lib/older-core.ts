import { createContext } from "react"

/**
 * Opens "Package and version" when the instance runs a revision behind the hub's, so a method it
 * does not know means its core is older than the panel. Undefined otherwise.
 */
export const OlderCore = createContext<(() => void) | undefined>(undefined)
