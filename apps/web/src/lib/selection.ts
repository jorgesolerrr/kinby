import { useSyncExternalStore } from "react"

// What the page shows lives in the URL, so a reload or an opened link lands on it.
const SECTION = "instances"
const THREADS = "threads"
const CONFIG = "config"
export const CREATE_PATH = "/new"

const listeners = new Set<() => void>()

export function instancePath(instanceId: string): string {
  return `/${SECTION}/${encodeURIComponent(instanceId)}`
}

export function threadPath(instanceId: string, threadId: string): string {
  return `${instancePath(instanceId)}/${THREADS}/${encodeURIComponent(threadId)}`
}

function configPath(instanceId: string): string {
  return `${instancePath(instanceId)}/${CONFIG}`
}

/** Select an instance without reloading the page, as a new entry in the browser's history. */
export function selectInstance(instanceId: string): void {
  navigate(instancePath(instanceId))
}

/** Select one of an instance's threads the same way. */
export function selectThread(instanceId: string, threadId: string): void {
  navigate(threadPath(instanceId, threadId))
}

/** Open an instance's config panel the same way. */
export function openConfig(instanceId: string): void {
  navigate(configPath(instanceId))
}

/** Open the create wizard the same way. */
export function openCreateWizard(): void {
  navigate(CREATE_PATH)
}

/** The hub instance ID the URL names, if it names one. */
export function useSelectedInstanceId(): string | undefined {
  return useSyncExternalStore(subscribe, selectedInstanceId)
}

/** The thread the URL names under its instance, if it names one. */
export function useSelectedThreadId(): string | undefined {
  return useSyncExternalStore(subscribe, selectedThreadId)
}

/** Whether the URL opens its instance's config panel. */
export function useConfigOpen(): boolean {
  return useSyncExternalStore(subscribe, configOpen)
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

function selectedThreadId(): string | undefined {
  const [, section, instanceId, threads, threadId] = window.location.pathname.split("/")
  return section === SECTION && instanceId && threads === THREADS && threadId
    ? decodeURIComponent(threadId)
    : undefined
}

function configOpen(): boolean {
  const [, section, instanceId, config] = window.location.pathname.split("/")
  return section === SECTION && Boolean(instanceId) && config === CONFIG
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
