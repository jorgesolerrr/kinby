// PROTOTYPE: lets the thread's takeover card and view markers open a side pane. Throwaway.
import { createContext, useContext } from "react"

export type Pane =
  | { kind: "takeover"; id: "takeover"; site: string; reason: string }
  | { kind: "view"; id: string; title: string }

export const PaneSlot = createContext<{
  open: (pane: Pane) => void
  isOpen: (id: string) => boolean
}>({ open: () => undefined, isOpen: () => false })

export const usePanes = () => useContext(PaneSlot)
