// PROTOTYPE: /proto/panes, two variants of the side panes beside a thread, inside the chosen shell
// (A · One sidebar) and thread (A · Prose). The last turn waits on a takeover; its card opens the
// browser pane, and the "Changed the view Trips" marker opens the view. ?variant=A|B picks the
// variant; the floating bar and the arrow keys switch it. Mock data, no hub. Throwaway: lives on
// prototype/panes only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import { type Screen, instanceById } from "../shell/mock"
import { ThreadSlot } from "../shell/screens"
import { VariantA as Shell } from "../shell/variant-a"
import { HISTORY, lastTurn } from "../thread/mock"
import { ComposerA, ThreadA } from "../thread/variant-a"
import { type Pane, PaneSlot } from "./slot"
import { PanesA } from "./variant-a"
import { PanesB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "Split", Panes: PanesA },
  { key: "B", name: "Stage", Panes: PanesB },
] as const

const TAKEOVER: Pane = {
  kind: "takeover",
  id: "takeover",
  site: "thefork.pt",
  reason: "TheFork wants you to sign in before it takes the booking.",
}
const TRIPS: Pane = { kind: "view", id: "trips", title: "Trips" }

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string) {
  const url = new URL(window.location.href)
  url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

export function PanesPrototype() {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      VARIANTS.findIndex((v) => v.key === param("variant")),
    ),
  )
  const [panes, setPanes] = useState<Pane[]>([])
  const [active, setActive] = useState<string>()
  const [screen, setScreen] = useState<Screen>({
    kind: "thread",
    instanceId: "kin",
    threadId: "k1",
  })
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
      if (t.closest("input, textarea, [contenteditable], [role=dialog], [role=menu]")) return
      if (e.key === "ArrowLeft") step(-1)
      if (e.key === "ArrowRight") step(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [step])

  // A keeps one pane: opening another replaces it. B keeps each as a tab.
  const open = (pane: Pane) => {
    setPanes((all) =>
      variant.key === "A" ? [pane] : all.some((p) => p.id === pane.id) ? all : [...all, pane],
    )
    setActive(pane.id)
  }
  const close = (id: string) => {
    setPanes((all) => {
      const rest = all.filter((p) => p.id !== id)
      setActive(rest.at(-1)?.id)
      return rest
    })
  }

  const turns = [...HISTORY, lastTurn("takeover", 0)].filter((t) => t !== undefined)
  const { Panes } = variant

  return (
    <TooltipProvider>
      <PaneSlot.Provider value={{ open, isOpen: (id) => panes.some((p) => p.id === id) }}>
        <ThreadSlot.Provider
          value={{
            thread: (instanceId) => (
              <Panes
                panes={panes}
                active={active}
                setActive={setActive}
                close={close}
                thread={
                  <ThreadA
                    instance={instanceById(instanceId)}
                    turns={instanceId === "kin" ? turns : []}
                    streaming={false}
                    notice={false}
                  />
                }
              />
            ),
            composer: (instanceId, pickTo) => (
              <ComposerA key={instanceId} instanceId={instanceId} pick={pickTo} />
            ),
          }}
        >
          <Shell screen={screen} go={setScreen} />
        </ThreadSlot.Provider>
      </PaneSlot.Provider>
      {import.meta.env.DEV && (
        <div className="fixed top-2 right-2 z-[60] flex items-center gap-1 rounded-lg bg-foreground p-1 text-xs text-background shadow-lg">
          <button
            type="button"
            aria-label="Previous variant"
            className="rounded-md p-1.5 hover:bg-background/20"
            onClick={() => step(-1)}
          >
            <ChevronLeftIcon className="size-4" />
          </button>
          <b className="px-1 whitespace-nowrap">
            {variant.key} · {variant.name}
          </b>
          <button
            type="button"
            aria-label="Next variant"
            className="rounded-md p-1.5 hover:bg-background/20"
            onClick={() => step(1)}
          >
            <ChevronRightIcon className="size-4" />
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 hover:bg-background/20"
            onClick={() => open(TAKEOVER)}
          >
            Browser
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 hover:bg-background/20"
            onClick={() => open(TRIPS)}
          >
            View
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 hover:bg-background/20"
            onClick={() => {
              setPanes([])
              setActive(undefined)
            }}
          >
            Close all
          </button>
        </div>
      )}
    </TooltipProvider>
  )
}
