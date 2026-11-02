// PROTOTYPE: /proto/library, two variants of the Library page inside the chosen shell
// (A · One sidebar). ?variant=A|B picks the variant and ?kind=skill|mcp_server|factory the kind
// it opens on. The floating bar and the arrow keys switch the variant. Mock data, no hub, nothing
// saves. Throwaway: lives on prototype/library only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import type { Screen } from "../shell/mock"
import { LibrarySlot } from "../shell/screens"
import { VariantA as Shell } from "../shell/variant-a"
import { KINDS, type Kind } from "./mock"
import { LibraryA } from "./variant-a"
import { LibraryB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "Tabs + rows" },
  { key: "B", name: "List + detail" },
] as const

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string | undefined) {
  const url = new URL(window.location.href)
  if (value === undefined) url.searchParams.delete(name)
  else url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

export function LibraryPrototype() {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      VARIANTS.findIndex((v) => v.key === param("variant")),
    ),
  )
  const [kind, setKind] = useState<Kind>(
    () => KINDS.find((k) => k.kind === param("kind"))?.kind ?? "skill",
  )
  const [screen, setScreen] = useState<Screen>({ kind: "library" })
  const variant = VARIANTS[index]

  const pickKind = (k: Kind) => {
    setKind(k)
    setParam("kind", k === "skill" ? undefined : k)
  }

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
      if (t.closest("input, textarea, [contenteditable], [role=menu], [role=listbox], [role=tab]"))
        return
      if (e.key === "ArrowLeft") step(-1)
      if (e.key === "ArrowRight") step(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [step])

  return (
    <TooltipProvider>
      <LibrarySlot.Provider
        value={(go) =>
          variant.key === "A" ? (
            <LibraryA go={go} kind={kind} onKind={pickKind} />
          ) : (
            <LibraryB key={kind} go={go} kind={kind} />
          )
        }
      >
        <Shell screen={screen} go={setScreen} />
      </LibrarySlot.Provider>
      {import.meta.env.DEV && (
        <div
          data-proto-bar
          className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-lg bg-foreground p-1 text-sm text-background shadow-lg"
        >
          <button
            type="button"
            aria-label="Previous variant"
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
            aria-label="Next variant"
            className="rounded-md p-1.5 hover:bg-background/15"
            onClick={() => step(1)}
          >
            <ChevronRightIcon className="size-4" />
          </button>
          {screen.kind !== "library" && (
            <>
              <span className="mx-1 h-4 w-px bg-background/30" />
              <button
                type="button"
                className="rounded-md px-2 py-1 hover:bg-background/15"
                onClick={() => setScreen({ kind: "library" })}
              >
                Back to Library
              </button>
            </>
          )}
        </div>
      )}
    </TooltipProvider>
  )
}
