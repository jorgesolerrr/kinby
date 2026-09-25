import { createThemeStore } from "@/lib/theme"

export const theme = createThemeStore(
  window.localStorage,
  window.matchMedia("(prefers-color-scheme: dark)"),
  (dark) => document.documentElement.classList.toggle("dark", dark),
)
