import { useSyncExternalStore } from "react"

// The selected instance lives in the URL, so a reload or an opened link lands on it.
const SECTION = "instances"

const listeners = new Set<() => void>()

export function instancePath(instanceId: string): string {
  return `/${SECTION}/${encodeURIComponent(instanceId)}`
}

/** Select an instance without reloading the page, as a new entry in the browser's history. */
export function selectInstance(instanceId: string): void {
  window.history.pushState(null, "", instancePath(instanceId))
  for (const listener of listeners) listener()
}

/** The hub instance ID the URL names, if it names one. */
export function useSelectedInstanceId(): string | undefined {
  return useSyncExternalStore(subscribe, selectedInstanceId)
}

function selectedInstanceId(): string | undefined {
  const [, section, instanceId] = window.location.pathname.split("/")
  return section === SECTION && instanceId ? decodeURIComponent(instanceId) : undefined
}

// Back and forward change the URL too.
function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener("popstate", listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener("popstate", listener)
  }
}
