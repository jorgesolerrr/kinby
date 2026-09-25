import { useSyncExternalStore } from "react"

const THEMES = ["light", "dark", "system"] as const
export type Theme = (typeof THEMES)[number]

const STORAGE_KEY = "kinby-theme"

type ThemeStorage = Pick<Storage, "getItem" | "setItem">

interface SystemScheme {
  readonly matches: boolean
  addEventListener(type: "change", listener: () => void): void
}

export interface ThemeStore {
  subscribe: (listener: () => void) => () => void
  getTheme: () => Theme
  setTheme: (theme: Theme) => void
}

export function isTheme(value: string | null): value is Theme {
  return THEMES.some((theme) => theme === value)
}

/** Hold the user's theme, and keep the page dark whenever it or the system says so. */
export function createThemeStore(
  storage: ThemeStorage,
  systemDark: SystemScheme,
  applyDark: (dark: boolean) => void,
): ThemeStore {
  const stored = storage.getItem(STORAGE_KEY)
  let theme: Theme = isTheme(stored) ? stored : "system"
  const listeners = new Set<() => void>()
  const apply = () => applyDark(theme === "dark" || (theme === "system" && systemDark.matches))

  systemDark.addEventListener("change", apply)
  apply()

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getTheme: () => theme,
    setTheme: (next) => {
      theme = next
      storage.setItem(STORAGE_KEY, next)
      apply()
      for (const listener of listeners) listener()
    },
  }
}

export function useTheme(store: ThemeStore): Theme {
  return useSyncExternalStore(store.subscribe, store.getTheme)
}
