// PROTOTYPE, round two: design variants over fixtures, switchable with ?variant=. Dev builds only
// (main.tsx mounts it under import.meta.env.DEV).
//   /prototype/sidebar  the agents in the sidebar and their threads in a column beside it (A–C)
//   /prototype/library  the hub's Library using the whole width (A–C)
//   /prototype/secrets  the hub's Secrets using the whole width (A–C)
// Lives on the prototype branch; the winners get rebuilt properly in the real files.
import { TooltipProvider } from "@/components/ui/tooltip"

import * as LA from "./library-a-split"
import * as LB from "./library-b-table"
import * as LC from "./library-c-shelves"
import * as XA from "./secrets-a-table"
import * as XB from "./secrets-b-matrix"
import * as XC from "./secrets-c-split"
import * as SA from "./sidebar-a-columns"
import * as SB from "./sidebar-b-folding"
import * as SC from "./sidebar-c-tree"
import { useParam } from "./param"
import { PrototypeSwitcher } from "./switcher"

const SURFACES = {
  sidebar: [
    { key: "A", name: SA.name, Component: SA.SidebarA },
    { key: "B", name: SB.name, Component: SB.SidebarB },
    { key: "C", name: SC.name, Component: SC.SidebarC },
  ],
  library: [
    { key: "A", name: LA.name, Component: LA.LibraryA },
    { key: "B", name: LB.name, Component: LB.LibraryB },
    { key: "C", name: LC.name, Component: LC.LibraryC },
  ],
  secrets: [
    { key: "A", name: XA.name, Component: XA.SecretsA },
    { key: "B", name: XB.name, Component: XB.SecretsB },
    { key: "C", name: XC.name, Component: XC.SecretsC },
  ],
}

export default function Prototype() {
  const surface = window.location.pathname.split("/")[2] as keyof typeof SURFACES
  const variants = SURFACES[surface] ?? SURFACES.sidebar
  const key = useParam("variant", "A")
  const entry = variants.find((variant) => variant.key === key) ?? variants[0]
  return (
    <TooltipProvider>
      <entry.Component />
      <PrototypeSwitcher variants={variants} current={entry.key} />
    </TooltipProvider>
  )
}
