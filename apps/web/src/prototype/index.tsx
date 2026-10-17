// PROTOTYPE: design variants for kinby's web app, switchable with ?variant=. Dev builds only
// (main.tsx mounts it under import.meta.env.DEV).
//   /prototype/components  four component directions (A–D) and the approved merge (E)
//   /prototype/layout      four app shells (A–D) built from the approved components
// Lives on the prototype branch; the winners get rebuilt properly in the real files.
import { TooltipProvider } from "@/components/ui/tooltip"

import * as A from "./components-a-baseline"
import * as B from "./components-b-document"
import * as C from "./components-c-console"
import * as D from "./components-d-companion"
import * as E from "./components-e-merged"
import * as LA from "./layout-a-sidebar"
import * as LB from "./layout-b-three-pane"
import * as LC from "./layout-c-inbox"
import * as LD from "./layout-d-home-split"
import { PrototypeSwitcher, useParam } from "./switcher"

const COMPONENTS = [
  { key: "A", name: A.name, Component: A.ComponentsA },
  { key: "B", name: B.name, Component: B.ComponentsB },
  { key: "C", name: C.name, Component: C.ComponentsC },
  { key: "D", name: D.name, Component: D.ComponentsD },
  { key: "E", name: E.name, Component: E.ComponentsE },
]

const LAYOUTS = [
  { key: "A", name: LA.name, Component: LA.LayoutA },
  { key: "B", name: LB.name, Component: LB.LayoutB },
  { key: "C", name: LC.name, Component: LC.LayoutC },
  { key: "D", name: LD.name, Component: LD.LayoutD },
]

export default function Prototype() {
  const layouts = window.location.pathname.startsWith("/prototype/layout")
  const variants = layouts ? LAYOUTS : COMPONENTS
  const key = useParam("variant", layouts ? "A" : "E")
  const entry = variants.find((variant) => variant.key === key) ?? variants[0]
  return (
    <TooltipProvider>
      <entry.Component />
      <PrototypeSwitcher variants={variants} current={entry.key} />
    </TooltipProvider>
  )
}
