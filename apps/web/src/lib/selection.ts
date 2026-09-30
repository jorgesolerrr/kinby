import { useSyncExternalStore } from "react"

// What the page shows lives in the URL, so a reload or an opened link lands on it.
const SECTION = "instances"
const THREADS = "threads"
const CONFIG = "config"
const ROUTINES = "routines"
const PACKAGE = "package"
const MEMORY = "memory"
const STATS = "stats"
export const CREATE_PATH = "/new"
export const USAGE_PATH = "/usage"

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

/** One routine in an instance's config panel. */
export function routinePath(instanceId: string, name: string): string {
  return `${configPath(instanceId)}/${ROUTINES}/${encodeURIComponent(name)}`
}

function packagePath(instanceId: string): string {
  return `${configPath(instanceId)}/${PACKAGE}`
}

function memoryPath(instanceId: string): string {
  return `${instancePath(instanceId)}/${MEMORY}`
}

export function statsPath(instanceId: string): string {
  return `${instancePath(instanceId)}/${STATS}`
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

/** Open a routine in an instance's config panel the same way. */
export function openRoutine(instanceId: string, name: string): void {
  navigate(routinePath(instanceId, name))
}

/** Open an instance's config panel on "Package and version" the same way. */
export function openPackage(instanceId: string): void {
  navigate(packagePath(instanceId))
}

/**
 * Drop the routine or the section a link named from the URL and keep the config panel, in the same
 * history entry.
 */
export function leaveLink(instanceId: string): void {
  const path = configPath(instanceId)
  if (window.location.pathname === path) return
  window.history.replaceState(null, "", path)
  changed()
}

/** Open an instance's memory page the same way. */
export function openMemory(instanceId: string): void {
  navigate(memoryPath(instanceId))
}

/** Open an instance's stats page the same way. */
export function openStats(instanceId: string): void {
  navigate(statsPath(instanceId))
}

/** Open the create wizard the same way. */
export function openCreateWizard(): void {
  navigate(CREATE_PATH)
}

/** Open the hub's usage page the same way. */
export function openUsage(): void {
  navigate(USAGE_PATH)
}

/** The URL's path, which changes with every page, instance, and thread picked. */
export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname)
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

/** Whether the URL opens its instance's memory page. */
export function useMemoryOpen(): boolean {
  return useSyncExternalStore(subscribe, memoryOpen)
}

/** Whether the URL opens its instance's stats page. */
export function useStatsOpen(): boolean {
  return useSyncExternalStore(subscribe, statsOpen)
}

/** Whether the URL opens the create wizard. */
export function useCreating(): boolean {
  return useSyncExternalStore(subscribe, () => window.location.pathname === CREATE_PATH)
}

/** Whether the URL opens the hub's usage page. */
export function useUsageOpen(): boolean {
  return useSyncExternalStore(subscribe, () => window.location.pathname === USAGE_PATH)
}

function navigate(path: string): void {
  window.history.pushState(null, "", path)
  changed()
}

function changed(): void {
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

/** The routine the URL opens in its instance's config panel, if it names one. */
export function linkedRoutine(): string | undefined {
  const [, section, instanceId, config, routines, name] = window.location.pathname.split("/")
  return section === SECTION && instanceId && config === CONFIG && routines === ROUTINES && name
    ? decodeURIComponent(name)
    : undefined
}

/** Whether the URL opens its instance's config panel on "Package and version". */
export function linkedPackage(): boolean {
  const [, section, instanceId, config, linked] = window.location.pathname.split("/")
  return section === SECTION && Boolean(instanceId) && config === CONFIG && linked === PACKAGE
}

function memoryOpen(): boolean {
  const [, section, instanceId, memory] = window.location.pathname.split("/")
  return section === SECTION && Boolean(instanceId) && memory === MEMORY
}

function statsOpen(): boolean {
  const [, section, instanceId, stats] = window.location.pathname.split("/")
  return section === SECTION && Boolean(instanceId) && stats === STATS
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
