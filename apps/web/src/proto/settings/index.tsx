// PROTOTYPE: /proto/settings, two variants of the Settings dialog's sections, inside the chosen
// shell (A · One sidebar). The dialog opens on load; the account menu's Settings opens it again.
// ?variant=A|B picks the variant and ?section= the section; the floating bar and the arrow keys
// switch the variant, and its Full/Empty toggle shows every section with no data. Mock data, no
// hub, nothing saves. Throwaway: lives on prototype/settings only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import type { Screen } from "../shell/mock"
import { VariantA as Shell } from "../shell/variant-a"
import { SECTIONS, type Section } from "./mock"
import { SettingsA } from "./variant-a"
import { SettingsB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "Rows", Settings: SettingsA },
  { key: "B", name: "Drill-in", Settings: SettingsB },
] as const

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string | undefined) {
  const url = new URL(window.location.href)
  if (value === undefined) url.searchParams.delete(name)
  else url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

const isSection = (value: string | null): value is Section => SECTIONS.some((s) => s.id === value)

export function SettingsPrototype() {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      VARIANTS.findIndex((v) => v.key === param("variant")),
    ),
  )
  const [section, setSection] = useState<Section | undefined>(() => {
    const asked = param("section")
    return isSection(asked) ? asked : "general"
  })
  const [empty, setEmpty] = useState(() => param("empty") === "1")
  const [screen, setScreen] = useState<Screen>({ kind: "home" })
  const variant = VARIANTS[index]

  const step = useCallback((by: number) => {
    setIndex((i) => {
      const next = (i + by + VARIANTS.length) % VARIANTS.length
      setParam("variant", VARIANTS[next].key)
      return next
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest("input, textarea, [contenteditable], [role=menu], [role=listbox]")) return
      if (e.key === "ArrowLeft") step(-1)
      if (e.key === "ArrowRight") step(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [step])

  const open = (picked: Section | undefined) => {
    setSection(picked)
    setParam("section", picked)
  }

  const { Settings } = variant
  return (
    <TooltipProvider>
      <Shell screen={screen} go={setScreen} onSettings={() => open("general")} />
      <Settings
        key={variant.key}
        section={section}
        onSection={open}
        onClose={() => open(undefined)}
        empty={empty}
      />
      {import.meta.env.DEV && (
        <div
          data-proto-bar
          className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-lg bg-foreground p-1 text-sm text-background shadow-lg"
        >
          <button
            type="button"
            className="rounded-md p-1.5 hover:bg-background/15"
            onClick={() => step(-1)}
          >
            <ChevronLeftIcon className="size-4" />
          </button>
          <span className="px-2 font-medium whitespace-nowrap">
            {variant.key} · {variant.name}
          </span>
          <button
            type="button"
            className="rounded-md p-1.5 hover:bg-background/15"
            onClick={() => step(1)}
          >
            <ChevronRightIcon className="size-4" />
          </button>
          <span className="mx-1 h-4 w-px bg-background/30" />
          <button
            type="button"
            className="rounded-md px-2 py-1 hover:bg-background/15"
            onClick={() => {
              setEmpty(!empty)
              setParam("empty", empty ? undefined : "1")
            }}
          >
            {empty ? "Empty" : "Full"}
          </button>
          {section === undefined && (
            <button
              type="button"
              className="rounded-md px-2 py-1 hover:bg-background/15"
              onClick={() => open("general")}
            >
              Open Settings
            </button>
          )}
        </div>
      )}
    </TooltipProvider>
  )
}
