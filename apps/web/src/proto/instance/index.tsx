// PROTOTYPE: /proto/instance, two variants of the instance page and its tabs, inside the chosen
// shell (A · One sidebar). It opens on Kin's page; ?variant=A|B picks the variant, ?tab= the tab
// and ?state= the instance state (running, stopped, update, failed). The floating bar and the
// arrow keys switch the variant. Mock data, no hub, nothing saves. Throwaway: lives on
// prototype/instance only.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { TooltipProvider } from "@/components/ui/tooltip"
import "@/theme"

import { type InstanceTab, INSTANCE_TABS, type Screen } from "../shell/mock"
import { InstanceSlot } from "../shell/screens"
import { VariantA as Shell } from "../shell/variant-a"
import { type Life, LIVES } from "./mock"
import { InstanceA } from "./variant-a"
import { InstanceB } from "./variant-b"

const VARIANTS = [
  { key: "A", name: "Tabs + rows", Page: InstanceA },
  { key: "B", name: "Overview + section nav", Page: InstanceB },
] as const

const param = (name: string) => new URLSearchParams(window.location.search).get(name)

function setParam(name: string, value: string | undefined) {
  const url = new URL(window.location.href)
  if (value === undefined) url.searchParams.delete(name)
  else url.searchParams.set(name, value)
  window.history.replaceState(null, "", url)
}

const isTab = (value: string | null): value is InstanceTab =>
  INSTANCE_TABS.some((t) => t.id === value)

export function InstancePrototype() {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      VARIANTS.findIndex((v) => v.key === param("variant")),
    ),
  )
  const [screen, setScreen] = useState<Screen>(() => {
    const tab = param("tab")
    return { kind: "instance", instanceId: "kin", tab: isTab(tab) ? tab : "overview" }
  })
  const [life, setLife] = useState<Life>(() => {
    const asked = param("state")
    return LIVES.find((l) => l === asked) ?? "running"
  })
  const variant = VARIANTS[index]

  const go = (to: Screen) => {
    setScreen(to)
    setParam("tab", to.kind === "instance" ? to.tab : undefined)
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

  const { Page } = variant
  return (
    <TooltipProvider>
      <InstanceSlot.Provider
        value={(instanceId, tab, to) => (
          <Page
            key={`${variant.key}-${instanceId}`}
            instanceId={instanceId}
            tab={tab}
            go={to}
            life={instanceId === "kin" ? life : undefined}
            onLife={setLife}
          />
        )}
      >
        <Shell screen={screen} go={go} />
      </InstanceSlot.Provider>
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
          <span className="mx-1 h-4 w-px bg-background/30" />
          {LIVES.map((l) => (
            <button
              key={l}
              type="button"
              className={`rounded-md px-2 py-1 hover:bg-background/15 ${l === life ? "bg-background/20" : ""}`}
              onClick={() => {
                setLife(l)
                setParam("state", l === "running" ? undefined : l)
              }}
            >
              {l}
            </button>
          ))}
        </div>
      )}
    </TooltipProvider>
  )
}
