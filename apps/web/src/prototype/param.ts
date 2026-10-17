// PROTOTYPE: the ?variant= and ?theme= search params, read and written in place.
import { useSyncExternalStore } from "react"

const listeners = new Set<() => void>()
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  window.addEventListener("popstate", listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener("popstate", listener)
  }
}

export function setParam(name: string, value: string) {
  const url = new URL(window.location.href)
  url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
  for (const listener of listeners) listener()
}

export function useParam(name: string, fallback: string): string {
  return useSyncExternalStore(
    subscribe,
    () => new URL(window.location.href).searchParams.get(name) ?? fallback,
  )
}
