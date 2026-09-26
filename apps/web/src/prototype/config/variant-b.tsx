// PROTOTYPE, throwaway. Variant B: two panes. A grouped section list on the left with what needs
// attention, the selected section on the right.
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"

import type { Config } from "./flow"
import { RecreateNotice, SECTIONS } from "./parts"

export const name = "Two panes"

const GROUPS = [
  { label: "Behavior", keys: ["behavior", "recap", "permissions", "manifest"] },
  { label: "Capabilities", keys: ["routines", "skills", "tools"] },
  { label: "Instance", keys: ["secrets", "package-config", "version"] },
]

export function VariantB({ config }: { config: Config }) {
  const [selected, setSelected] = useState("behavior")
  const section = SECTIONS.find((s) => s.key === selected) ?? SECTIONS[0]
  return (
    <div className="flex min-h-0 flex-1">
      <nav className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r p-3">
        {GROUPS.map((g) => (
          <div key={g.label} className="flex flex-col gap-1">
            <span className="px-3 text-xs font-medium text-muted-foreground">{g.label}</span>
            <ItemGroup>
              {g.keys.map((key) => {
                const s = SECTIONS.find((x) => x.key === key)
                if (!s) return null
                const attention = s.attention?.(config)
                return (
                  <Item
                    key={key}
                    size="xs"
                    variant={key === selected ? "muted" : "default"}
                    render={
                      <button type="button" aria-label={s.label} onClick={() => setSelected(key)} />
                    }
                  >
                    <ItemMedia variant="icon">
                      <s.icon />
                    </ItemMedia>
                    <ItemContent>
                      <ItemTitle>{s.label}</ItemTitle>
                      {attention ? (
                        <Badge variant="destructive">{attention}</Badge>
                      ) : (
                        <ItemDescription>{s.hint}</ItemDescription>
                      )}
                    </ItemContent>
                  </Item>
                )
              })}
            </ItemGroup>
          </div>
        ))}
      </nav>
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
        <RecreateNotice config={config} />
        <div>
          <h1 className="text-xl font-semibold">{section.label}</h1>
          <p className="text-sm text-muted-foreground">{section.hint}</p>
        </div>
        <Separator />
        {section.render(config)}
      </main>
    </div>
  )
}
