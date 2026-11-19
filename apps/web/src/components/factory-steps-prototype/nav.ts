// PROTOTYPE (#1450), throwaway. Where the prototype is, as `?variant=A&at=step:implement&from=r-1412`,
// so a reload or a shared link lands there.
import { useSyncExternalStore } from "react"

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => {
  listeners.add(l)
  window.addEventListener("popstate", l)
  return () => {
    listeners.delete(l)
    window.removeEventListener("popstate", l)
  }
}

export const param = (name: string) => new URLSearchParams(window.location.search).get(name)

export function useParam(name: string): string | null {
  return useSyncExternalStore(subscribe, () => param(name))
}

export function setParams(values: Record<string, string | null>, push: boolean) {
  const params = new URLSearchParams(window.location.search)
  for (const [name, value] of Object.entries(values)) {
    if (value === null) params.delete(name)
    else params.set(name, value)
  }
  const url = `${window.location.pathname}?${params}`
  if (push) window.history.pushState(null, "", url)
  else window.history.replaceState(null, "", url)
  notify()
}

/** The screen open: "factory", "step:<id>", "add:<after>" or "run". */
export function useAt(): [kind: string, id: string] {
  const at = useParam("at") ?? "factory"
  const [kind, ...rest] = at.split(":")
  return [kind, rest.join(":")]
}

/** Open a screen. `from` keeps the run the user came from, so the editor can send them back. */
export function go(at: string, from: string | null = param("from")) {
  setParams({ at, from }, true)
  window.scrollTo(0, 0)
}
