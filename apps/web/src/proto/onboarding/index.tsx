// PROTOTYPE: /proto/onboarding, two variants of each first-run surface inside the chosen shell
// (A · One sidebar). ?screen= picks the surface (sign-in, first-run, new-instance, no-threads,
// stopped, empty-library) and ?variant=A|B the variant. The floating bar and the arrow keys
// switch the variant; its menu switches the screen. Signing in lands on the first run, and
// creating an instance lands on its empty thread screen. Mock data, no hub, nothing saves.
// Throwaway: lives on prototype/onboarding only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import type { Kind } from "../library/mock"
import { LibraryA, LibraryEmptySlot } from "../library/variant-a"
import { instanceById, type Screen } from "../shell/mock"
import { LibrarySlot, MainSlot } from "../shell/screens"
import { VariantA as Shell } from "../shell/variant-a"
import {
  FirstRunA,
  FirstRunB,
  LibraryEmptyA,
  LibraryEmptyB,
  NoThreadsA,
  NoThreadsB,
  StoppedA,
  StoppedB,
} from "./empty"
import { SCREENS, type ScreenId, stage, TARGET } from "./mock"
import { NewInstanceA, NewInstanceB } from "./new-instance"
import { SignInA, SignInB } from "./sign-in"

const VARIANTS: Record<ScreenId, [string, string]> = {
  "sign-in": ["Bare", "Split"],
  "first-run": ["Setup in place", "Get started"],
  "new-instance": ["One page", "Steps"],
  "no-threads": ["Greeting", "Starters"],
  stopped: ["Composer stays", "Empty + Start"],
  "empty-library": ["Empty block", "Row in place"],
}

const OPENS_ON: Record<ScreenId, Screen> = {
  "sign-in": { kind: "home" },
  "first-run": { kind: "home" },
  "new-instance": { kind: "new-instance" },
  "no-threads": { kind: "home" },
  stopped: { kind: "home" },
  "empty-library": { kind: "library" },
}

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string) {
  const url = new URL(window.location.href)
  url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

export function OnboardingPrototype() {
  const [id, setId] = useState<ScreenId>(() => {
    const picked = SCREENS.find((s) => s.id === param("screen"))?.id ?? "sign-in"
    stage(picked)
    return picked
  })
  const [b, setB] = useState(param("variant") === "B")
  const [screen, setScreen] = useState<Screen>(OPENS_ON[id])
  const [kind, setKind] = useState<Kind>(id === "empty-library" ? "mcp_server" : "skill")
  const [mount, setMount] = useState(0)

  const pick = useCallback((next: ScreenId) => {
    stage(next)
    setId(next)
    setScreen(OPENS_ON[next])
    setKind(next === "empty-library" ? "mcp_server" : "skill")
    setMount((m) => m + 1)
    setParam("screen", next)
  }, [])

  const flip = useCallback(() => {
    setB((on) => {
      setParam("variant", on ? "A" : "B")
      return !on
    })
    setMount((m) => m + 1)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (
        t.closest(
          "input, textarea, select, [contenteditable], [role=menu], [role=listbox], [role=tab]",
        )
      )
        return
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") flip()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [flip])

  const target = instanceById(TARGET[id])

  const main = (s: Screen, go: (s: Screen) => void) => {
    if (s.kind === "new-instance") {
      const Page = b ? NewInstanceB : NewInstanceA
      return <Page key={mount} onDone={() => pick("no-threads")} />
    }
    if (s.kind !== "home") return undefined
    if (id === "first-run")
      return b ? <FirstRunB home={target} go={go} /> : <FirstRunA home={target} />
    if (id === "no-threads")
      return b ? <NoThreadsB instance={target} /> : <NoThreadsA instance={target} />
    if (id === "stopped")
      return b ? <StoppedB instance={target} go={go} /> : <StoppedA instance={target} />
    return undefined
  }

  const page =
    id === "sign-in" ? (
      b ? (
        <SignInB onSignedIn={() => pick("first-run")} />
      ) : (
        <SignInA onSignedIn={() => pick("first-run")} />
      )
    ) : (
      <MainSlot.Provider value={main}>
        <LibraryEmptySlot.Provider
          value={(k, add) =>
            b ? (
              <LibraryEmptyB kind={k} add={add} />
            ) : (
              <LibraryEmptyA kind={k} add={add} go={setScreen} />
            )
          }
        >
          <LibrarySlot.Provider value={(go) => <LibraryA go={go} kind={kind} onKind={setKind} />}>
            <Shell key={`${id}-${mount}`} screen={screen} go={setScreen} />
          </LibrarySlot.Provider>
        </LibraryEmptySlot.Provider>
      </MainSlot.Provider>
    )

  return (
    <TooltipProvider>
      {page}
      {import.meta.env.DEV && (
        <div
          data-proto-bar
          className="fixed bottom-4 left-1/2 z-[100] flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-1 rounded-lg bg-foreground p-1 text-sm text-background shadow-lg"
        >
          <button
            type="button"
            aria-label="Previous variant"
            className="rounded-md p-1.5 hover:bg-background/15"
            onClick={flip}
          >
            <ChevronLeftIcon className="size-4" />
          </button>
          <span className="px-2 font-medium whitespace-nowrap">
            {b ? "B" : "A"} · {VARIANTS[id][b ? 1 : 0]}
          </span>
          <button
            type="button"
            aria-label="Next variant"
            className="rounded-md p-1.5 hover:bg-background/15"
            onClick={flip}
          >
            <ChevronRightIcon className="size-4" />
          </button>
          <span className="mx-1 h-4 w-px bg-background/30" />
          <select
            aria-label="Screen"
            value={id}
            onChange={(e) => pick(e.target.value as ScreenId)}
            className="min-w-0 rounded-md bg-transparent px-1 py-1 text-sm hover:bg-background/15"
          >
            {SCREENS.map((s) => (
              <option key={s.id} value={s.id} className="text-foreground">
                {s.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </TooltipProvider>
  )
}
