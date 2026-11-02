// PROTOTYPE: /proto/shell, two variants of kinby's app shell, switched by ?variant=A|B and a
// floating bar (or the arrow keys). Mock data, no hub. Throwaway: lives on prototype/shell only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme" // follows the system's light or dark

import { type Screen, describe } from "./mock"
import { VariantA } from "./variant-a"
import { VariantB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "One sidebar", Component: VariantA },
  { key: "B", name: "Rail + column", Component: VariantB },
] as const

function readVariant() {
  const key = new URLSearchParams(window.location.search).get("variant")
  return Math.max(
    0,
    VARIANTS.findIndex((v) => v.key === key),
  )
}

export function Shell() {
  const [index, setIndex] = useState(readVariant)
  const [screen, setScreen] = useState<Screen>({ kind: "home" })
  const variant = VARIANTS[index]

  const step = useCallback((by: number) => {
    setIndex((i) => {
      const next = (i + by + VARIANTS.length) % VARIANTS.length
      const url = new URL(window.location.href)
      url.searchParams.set("variant", VARIANTS[next].key)
      window.history.replaceState(null, "", url)
      return next
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest("input, textarea, [contenteditable], [role=dialog], [role=menu]")) return
      if (e.key === "ArrowLeft") step(-1)
      if (e.key === "ArrowRight") step(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [step])

  return (
    <TooltipProvider>
      <variant.Component screen={screen} go={setScreen} />
      {import.meta.env.DEV && (
        <div className="fixed top-2 right-2 z-50 flex items-center gap-1 rounded-full bg-foreground p-1 text-xs text-background shadow-lg">
          <button
            type="button"
            aria-label="Previous variant"
            className="rounded-full p-1.5 hover:bg-background/20"
            onClick={() => step(-1)}
          >
            <ChevronLeftIcon className="size-4" />
          </button>
          <span className="px-2 whitespace-nowrap">
            <b>
              {variant.key} · {variant.name}
            </b>
            <span className="opacity-60"> · {describe(screen)}</span>
          </span>
          <button
            type="button"
            aria-label="Next variant"
            className="rounded-full p-1.5 hover:bg-background/20"
            onClick={() => step(1)}
          >
            <ChevronRightIcon className="size-4" />
          </button>
        </div>
      )}
    </TooltipProvider>
  )
}
