import { useSyncExternalStore } from "react"

// What the page shows lives in the URL, so a reload or an opened link lands on it.
const SECTION = "instances"
const THREADS = "threads"
const TURNS = "turns"
const CONFIG = "config"
const ROUTINES = "routines"
const CORE = "core"
const SKILLS = "skills"
const MEMORY = "memory"
const STATS = "stats"
const VIEWS = "views"
export const CREATE_PATH = "/new"
export const LIBRARY_PATH = "/library"

/** An instance's page holds these tabs, each at its own path under the instance but Overview. */
const TABS = ["overview", CONFIG, ROUTINES, MEMORY, STATS] as const
export type InstanceTab = (typeof TABS)[number]

// The Settings dialog opens over whatever path is open, so its section is a search parameter.
const SETTINGS = "settings"
const SETTINGS_SECTIONS = ["general", "usage", "secrets", "channels", "instances"] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]

// The home screen's "Send to" opens on the instance this search parameter names.
const TO = "to"

/** The hub pages that became sections of the Settings dialog, by their old paths. */
const OLD_PATHS: Record<string, SettingsSection> = {
  "/usage": "usage",
  "/secrets": "secrets",
  "/channels": "channels",
  "/removed": "instances",
}

const listeners = new Set<() => void>()

function instancePath(instanceId: string): string {
  return `/${SECTION}/${encodeURIComponent(instanceId)}`
}

/** An instance's thread list page. */
export function threadsPath(instanceId: string): string {
  return `${instancePath(instanceId)}/${THREADS}`
}

export function threadPath(instanceId: string, threadId: string): string {
  return `${threadsPath(instanceId)}/${encodeURIComponent(threadId)}`
}

/** One turn of a thread, which the thread opens scrolled to. */
export function turnPath(instanceId: string, threadId: string, turnId: string): string {
  return `${threadPath(instanceId, threadId)}/${TURNS}/${encodeURIComponent(turnId)}`
}

export function tabPath(instanceId: string, tab: InstanceTab): string {
  return tab === "overview" ? instancePath(instanceId) : `${instancePath(instanceId)}/${tab}`
}

function configPath(instanceId: string): string {
  return tabPath(instanceId, CONFIG)
}

/** One routine on an instance's Routines tab. */
export function routinePath(instanceId: string, name: string): string {
  return `${tabPath(instanceId, ROUTINES)}/${encodeURIComponent(name)}`
}

/** The config sections a link opens the config panel on, by their path segment. */
export type ConfigLink = typeof CORE | typeof SKILLS

function linkPath(instanceId: string, link: ConfigLink): string {
  return `${configPath(instanceId)}/${link}`
}

function memoryPath(instanceId: string): string {
  return tabPath(instanceId, MEMORY)
}

/** One node open on an instance's memory page. */
export function nodePath(instanceId: string, node: string): string {
  return `${memoryPath(instanceId)}/${encodeURIComponent(node)}`
}

export function statsPath(instanceId: string): string {
  return tabPath(instanceId, STATS)
}

/** One of an instance's views, on its own page. */
export function viewPath(instanceId: string, viewId: string): string {
  return `${instancePath(instanceId)}/${VIEWS}/${encodeURIComponent(viewId)}`
}

/** Select an instance without reloading the page, as a new entry in the browser's history. */
export function selectInstance(instanceId: string): void {
  navigate(instancePath(instanceId))
}

/** Select one of an instance's threads the same way. */
export function selectThread(instanceId: string, threadId: string): void {
  navigate(threadPath(instanceId, threadId))
}

/** Select a thread scrolled to one of its turns the same way. */
export function selectTurn(instanceId: string, threadId: string, turnId: string): void {
  navigate(turnPath(instanceId, threadId, turnId))
}

/** Open one of an instance's tabs the same way. */
export function openTab(instanceId: string, tab: InstanceTab): void {
  navigate(tabPath(instanceId, tab))
}

/** Open an instance's config panel the same way. */
export function openConfig(instanceId: string): void {
  navigate(configPath(instanceId))
}

/** Open a routine on an instance's Routines tab the same way. */
export function openRoutine(instanceId: string, name: string): void {
  navigate(routinePath(instanceId, name))
}

/** Open an instance's config panel on "Core version" the same way. */
export function openCore(instanceId: string): void {
  navigate(linkPath(instanceId, CORE))
}

/** Open an instance's config panel on "Skills" the same way. */
export function openSkills(instanceId: string): void {
  navigate(linkPath(instanceId, SKILLS))
}

/** Drop the section a link named from the URL and keep the config panel, in the same history entry. */
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

/** Open a node on an instance's memory page the same way. */
export function openNode(instanceId: string, node: string): void {
  navigate(nodePath(instanceId, node))
}

/** Drop the linked node from the URL and keep the memory page, in the same history entry. */
export function leaveNode(instanceId: string): void {
  window.history.replaceState(null, "", memoryPath(instanceId))
  changed()
}

/** Open an instance's stats page the same way. */
export function openStats(instanceId: string): void {
  navigate(statsPath(instanceId))
}

/** Open one of an instance's views the same way. */
export function openView(instanceId: string, viewId: string): void {
  navigate(viewPath(instanceId, viewId))
}

/**
 * Open an instance, or one of its threads, in place of the current history entry, so Back skips the
 * page that opened it.
 */
export function landOn(instanceId: string, threadId?: string): void {
  const path = threadId === undefined ? instancePath(instanceId) : threadPath(instanceId, threadId)
  window.history.replaceState(null, "", path + window.location.search)
  changed()
}

/** The home screen, its "Send to" preset to `to` when it names an instance. */
export function homePath(to?: string): string {
  return to === undefined ? "/" : `/?${new URLSearchParams({ [TO]: to })}`
}

/** Go to the home screen, where no instance is selected, the same way. */
export function openHome(): void {
  navigate(homePath())
}

/** Open the onboarding the same way. */
export function openOnboarding(): void {
  navigate(CREATE_PATH)
}

/** Open the Settings dialog over the open page as a new history entry, so Back closes it. */
export function openSettings(section: SettingsSection = "general"): void {
  window.history.pushState(null, "", settingsUrl(section))
  changed()
}

/** Open another section of the Settings dialog in the same history entry. */
export function switchSettings(section: SettingsSection): void {
  window.history.replaceState(null, "", settingsUrl(section))
  changed()
}

/** Close the Settings dialog, keeping the page under it, in the same history entry. */
export function closeSettings(): void {
  window.history.replaceState(null, "", settingsUrl(undefined))
  changed()
}

/**
 * Replace an old path with the one that took its place: an old hub page's with the root path and
 * Settings open on its section, and a routine's in the config panel with its Routines tab path.
 */
export function replaceOldPath(): void {
  const section = OLD_PATHS[window.location.pathname]
  const [, root, instanceId, config, routines, name] = window.location.pathname.split("/")
  if (section !== undefined) {
    window.history.replaceState(null, "", `/?${SETTINGS}=${section}`)
  } else if (root === SECTION && instanceId && config === CONFIG && routines === ROUTINES && name) {
    const path = `/${SECTION}/${instanceId}/${ROUTINES}/${name}`
    window.history.replaceState(null, "", path + window.location.search)
  } else return
  changed()
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

/** The turn the URL names in its thread, if it names one. */
export function useSelectedTurnId(): string | undefined {
  return useSyncExternalStore(subscribe, selectedTurnId)
}

/** Whether the URL opens its instance's thread list page. */
export function useThreadsOpen(): boolean {
  return useSyncExternalStore(subscribe, threadsOpen)
}

/** The tab the URL opens on its instance's page, or none off the page, as on a thread or a view. */
export function useInstanceTab(): InstanceTab | undefined {
  return useSyncExternalStore(subscribe, openedTab)
}

/** The view the URL opens under its instance, if it names one. */
export function useViewOpen(): string | undefined {
  return useSyncExternalStore(subscribe, openedView)
}

/** Whether the URL opens the onboarding. */
export function useCreating(): boolean {
  return useSyncExternalStore(subscribe, () => window.location.pathname === CREATE_PATH)
}

/** Whether the URL opens the hub's library page. */
export function useLibraryOpen(): boolean {
  return useSyncExternalStore(subscribe, () => window.location.pathname === LIBRARY_PATH)
}

/** The section the URL opens the Settings dialog on, if it opens the dialog. */
export function useSettingsOpen(): SettingsSection | undefined {
  return useSyncExternalStore(subscribe, settingsOpen)
}

/** The instance the URL presets the home screen's "Send to" with, if it names one. */
export function useSendTo(): string | undefined {
  return useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(TO) ?? undefined,
  )
}

/** Open a page of the app by its path, without reloading the page, as a new history entry. */
export function navigate(path: string): void {
  window.history.pushState(null, "", path)
  changed()
}

function changed(): void {
  for (const listener of listeners) listener()
}

function selectedInstanceId(): string | undefined {
  const [, section, instanceId] = window.location.pathname.split("/")
  return section === SECTION && instanceId ? decoded(instanceId) : undefined
}

function selectedThreadId(): string | undefined {
  const [, section, instanceId, threads, threadId] = window.location.pathname.split("/")
  return section === SECTION && instanceId && threads === THREADS && threadId
    ? decoded(threadId)
    : undefined
}

function threadsOpen(): boolean {
  const [, section, instanceId, threads, threadId] = window.location.pathname.split("/")
  return section === SECTION && Boolean(instanceId) && threads === THREADS && !threadId
}

function selectedTurnId(): string | undefined {
  const [, , , , , turns, turnId] = window.location.pathname.split("/")
  return selectedThreadId() !== undefined && turns === TURNS && turnId ? decoded(turnId) : undefined
}

function openedTab(): InstanceTab | undefined {
  const [, section, instanceId, page] = window.location.pathname.split("/")
  if (section !== SECTION || !instanceId || page === THREADS || page === VIEWS) return undefined
  return TABS.find((tab) => tab === page) ?? "overview"
}

/** The routine the URL opens on its instance's Routines tab, if it names one. */
export function linkedRoutine(): string | undefined {
  const [, section, instanceId, routines, name] = window.location.pathname.split("/")
  return section === SECTION && instanceId && routines === ROUTINES && name
    ? decoded(name)
    : undefined
}

/** The section the URL opens its instance's config panel on, if it names one. */
export function linkedSection(): ConfigLink | undefined {
  const [, section, instanceId, config, linked] = window.location.pathname.split("/")
  if (section !== SECTION || !instanceId || config !== CONFIG) return undefined
  return linked === CORE || linked === SKILLS ? linked : undefined
}

/** The node the URL opens on its instance's memory page, if it names one. */
export function linkedNode(): string | undefined {
  const [, section, instanceId, memory, node] = window.location.pathname.split("/")
  return section === SECTION && instanceId && memory === MEMORY && node ? decoded(node) : undefined
}

function openedView(): string | undefined {
  const [, section, instanceId, views, viewId] = window.location.pathname.split("/")
  return section === SECTION && instanceId && views === VIEWS && viewId
    ? decoded(viewId)
    : undefined
}

function settingsOpen(): SettingsSection | undefined {
  const section = new URLSearchParams(window.location.search).get(SETTINGS)
  return SETTINGS_SECTIONS.find((known) => known === section)
}

/** The current path and its search, with the Settings dialog open on `section`, or closed. */
function settingsUrl(section: SettingsSection | undefined): string {
  const search = new URLSearchParams(window.location.search)
  if (section === undefined) search.delete(SETTINGS)
  else search.set(SETTINGS, section)
  const query = search.toString()
  return query === "" ? window.location.pathname : `${window.location.pathname}?${query}`
}

function decoded(part: string): string | undefined {
  try {
    return decodeURIComponent(part)
  } catch {
    return undefined
  }
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
