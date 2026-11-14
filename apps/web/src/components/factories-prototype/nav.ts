// PROTOTYPE (#1299), throwaway. Where the prototype is, as `?variant=A&at=run:r-1305`, so a reload
// or a shared link lands there.
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

export function setParam(name: string, value: string, push: boolean) {
  const params = new URLSearchParams(window.location.search)
  params.set(name, value)
  const url = `${window.location.pathname}?${params}`
  if (push) window.history.pushState(null, "", url)
  else window.history.replaceState(null, "", url)
  notify()
}

/** The screen open: "area", "line:<id>", "run:<id>", "owner:<id>" or "setup". */
export function useAt(): [kind: string, id: string] {
  const at = useParam("at") ?? "area"
  const [kind, ...rest] = at.split(":")
  return [kind, rest.join(":")]
}

export function go(at: string) {
  setParam("at", at, true)
  window.scrollTo(0, 0)
}
