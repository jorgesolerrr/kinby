import { useSyncExternalStore } from "react"

// What the page shows lives in the URL, so a reload or an opened link lands on it.
const SECTION = "instances"
export const CREATE_PATH = "/new"

const listeners = new Set<() => void>()

export function instancePath(instanceId: string): string {
  return `/${SECTION}/${encodeURIComponent(instanceId)}`
}

/** Select an instance without reloading the page, as a new entry in the browser's history. */
export function selectInstance(instanceId: string): void {
  navigate(instancePath(instanceId))
}

/** Open the create wizard the same way. */
export function openCreateWizard(): void {
  navigate(CREATE_PATH)
}

/** The hub instance ID the URL names, if it names one. */
export function useSelectedInstanceId(): string | undefined {
  return useSyncExternalStore(subscribe, selectedInstanceId)
}

/** Whether the URL opens the create wizard. */
export function useCreating(): boolean {
  return useSyncExternalStore(subscribe, () => window.location.pathname === CREATE_PATH)
}

function navigate(path: string): void {
  window.history.pushState(null, "", path)
  for (const listener of listeners) listener()
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
