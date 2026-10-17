// PROTOTYPE: the floating variant switcher. Not part of the design being judged.
import { ChevronLeftIcon, ChevronRightIcon, MoonIcon, SunIcon } from "lucide-react"
import { useEffect, useState } from "react"

import { setParam } from "./param"

export interface VariantEntry {
  key: string
  name: string
}

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(
    () =>
      new URL(window.location.href).searchParams.get("theme") === "dark" ||
      (new URL(window.location.href).searchParams.get("theme") === null &&
        window.matchMedia("(prefers-color-scheme: dark)").matches),
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
  }, [dark])
  return [
    dark,
    () => {
      setParam("theme", dark ? "light" : "dark")
      setDark(!dark)
    },
  ]
}

export function PrototypeSwitcher({
  variants,
  current,
}: {
  variants: VariantEntry[]
  current: string
}) {
  const index = Math.max(
    0,
    variants.findIndex((variant) => variant.key === current),
  )
  const [dark, toggleTheme] = useTheme()
  const go = (step: number) =>
    setParam("variant", variants[(index + step + variants.length) % variants.length].key)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.closest("input, textarea, [contenteditable=''], [contenteditable='true']")) return
      if (event.key === "ArrowLeft") go(-1)
      if (event.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  if (!import.meta.env.DEV) return null
  const entry = variants[index]
  return (
    <div
      role="toolbar"
      aria-label="Prototype variants"
      className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full bg-foreground p-1 text-sm text-background shadow-lg"
    >
      <button
        type="button"
        aria-label="Previous variant"
        onClick={() => go(-1)}
        className="grid size-8 place-items-center rounded-full outline-none hover:bg-background/15 focus-visible:ring-2 focus-visible:ring-ember"
      >
        <ChevronLeftIcon className="size-4" />
      </button>
      <output className="min-w-48 px-2 text-center font-medium">
        {entry.key} · {entry.name}
      </output>
      <button
        type="button"
        aria-label="Next variant"
        onClick={() => go(1)}
        className="grid size-8 place-items-center rounded-full outline-none hover:bg-background/15 focus-visible:ring-2 focus-visible:ring-ember"
      >
        <ChevronRightIcon className="size-4" />
      </button>
      <span aria-hidden className="mx-1 h-5 w-px bg-background/25" />
      <button
        type="button"
        aria-label={dark ? "Use light theme" : "Use dark theme"}
        onClick={toggleTheme}
        className="grid size-8 place-items-center rounded-full outline-none hover:bg-background/15 focus-visible:ring-2 focus-visible:ring-ember"
      >
        {dark ? <SunIcon className="size-4" /> : <MoonIcon className="size-4" />}
      </button>
    </div>
  )
}
