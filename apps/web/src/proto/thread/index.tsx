// PROTOTYPE: /proto/thread, two variants of a thread inside the chosen shell (A · One sidebar).
// ?variant=A|B picks the thread design, ?state= the last turn's state; the floating bar and the
// arrow keys switch both. Mock data, no hub. Throwaway: lives on prototype/thread only.
import { ChevronLeftIcon, ChevronRightIcon, RotateCcwIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import { type Screen, instanceById } from "../shell/mock"
import { ThreadSlot } from "../shell/screens"
import { VariantA as Shell } from "../shell/variant-a"
import { HISTORY, LAST_TURN, type LastTurn, STREAM_TICKS, lastTurn } from "./mock"
import { ComposerA, ThreadA } from "./variant-a"
import { ComposerB, ThreadB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "Prose", Thread: ThreadA, Composer: ComposerA },
  { key: "B", name: "Work log", Thread: ThreadB, Composer: ComposerB },
] as const

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string) {
  const url = new URL(window.location.href)
  url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

export function ThreadPrototype() {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      VARIANTS.findIndex((v) => v.key === param("variant")),
    ),
  )
  const [state, setState] = useState<LastTurn>(
    () => LAST_TURN.find((s) => s.key === param("state"))?.key ?? "done",
  )
  const [notice, setNotice] = useState(param("notice") === "1")
  const [tick, setTick] = useState(0)
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

  // The streaming state plays the last turn in, one tick every 400 ms.
  useEffect(() => {
    if (state !== "streaming" || tick > STREAM_TICKS) return
    const timer = window.setTimeout(() => setTick((t) => t + 1), tick < 4 ? 900 : 120)
    return () => window.clearTimeout(timer)
  }, [state, tick])

  const pick = (key: LastTurn) => {
    setState(key)
    setTick(0)
    setParam("state", key)
    setScreen({ kind: "thread", instanceId: "kin", threadId: "k1" })
  }

  const streaming = state === "streaming" && tick <= STREAM_TICKS
  const last = lastTurn(state, tick)
  const turns = state === "new" ? [] : last ? [...HISTORY, last] : HISTORY

  return (
    <TooltipProvider>
      <ThreadSlot.Provider
        value={{
          thread: (instanceId) => (
            <variant.Thread
              key={`${variant.key}-${state}`}
              instance={instanceById(instanceId)}
              turns={instanceId === "kin" ? turns : []}
              streaming={streaming}
              notice={notice}
            />
          ),
          composer: (instanceId, pickTo) => (
            <variant.Composer key={instanceId} instanceId={instanceId} pick={pickTo} />
          ),
        }}
      >
        <Shell screen={screen} go={setScreen} />
      </ThreadSlot.Provider>
      {import.meta.env.DEV && (
        <div className="fixed top-2 right-2 z-50 flex max-w-[calc(100vw-1rem)] flex-wrap items-center gap-1 rounded-lg bg-foreground p-1 text-xs text-background shadow-lg">
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
          <select
            aria-label="Last turn"
            className="rounded-md bg-background/15 px-1.5 py-1"
            value={state}
            onChange={(e) => pick(e.target.value as LastTurn)}
          >
            {LAST_TURN.map((s) => (
              <option key={s.key} value={s.key} className="text-foreground">
                {s.label}
              </option>
            ))}
          </select>
          {state === "streaming" && (
            <button
              type="button"
              aria-label="Replay"
              className="rounded-md p-1.5 hover:bg-background/20"
              onClick={() => setTick(0)}
            >
              <RotateCcwIcon className="size-4" />
            </button>
          )}
          <label className="flex items-center gap-1 px-1.5">
            <input
              type="checkbox"
              checked={notice}
              onChange={(e) => {
                setNotice(e.target.checked)
                setParam("notice", e.target.checked ? "1" : "0")
              }}
            />
            Notice
          </label>
        </div>
      )}
    </TooltipProvider>
  )
}
