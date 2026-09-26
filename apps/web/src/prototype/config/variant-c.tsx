// PROTOTYPE, throwaway. Variant C: an overview of cards, one line of state each. Editing opens the
// section in a side sheet, so the overview stays in view.
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

import type { Config } from "./flow"
import { RecreateNotice, SECTIONS } from "./parts"
import { INSTANCE } from "./stub"

export const name = "Overview + sheet"

function summary(key: string, config: Config): string {
  const firstLine = (file: keyof Config["files"]) => config.files[file].content.split("\n")[0]
  switch (key) {
    case "behavior":
      return firstLine("SYSTEM.md")
    case "recap":
      return firstLine("RECAP.md")
    case "permissions": {
      const c = config.files["permissions.toml"].content
      return `${c.match(/^mode = "(.*)"/m)?.[1]} by default, up to ${c.match(/^ceiling = "(.*)"/m)?.[1]}`
    }
    case "manifest": {
      const m = config.models()
      return `${m.main} · recap on ${m.recap}`
    }
    case "routines": {
      const on = config.routines.filter((r) => r.enabled)
      const next = on.find((r) => r.next_run)
      return `${on.length} of ${config.routines.length} on${next ? ` · next ${next.name} ${next.next_run}` : ""}`
    }
    case "skills":
      return `${new Set(config.skills.map((s) => s.name)).size} skills, ${config.skills.filter((s) => s.tier === "instance").length} customized`
    case "tools":
      return `${config.tools.length} tools, ${config.tools.filter((t) => t.writes).length} write`
    case "secrets":
      return `${config.secrets.filter((s) => s.is_set).length} of ${config.secrets.length} secrets set`
    case "package-config":
      return "check commands, skill picks"
    case "version":
      return `${INSTANCE.package.id} ${INSTANCE.package.installed_version} · kinby ${config.revision}`
    default:
      return ""
  }
}

export function VariantC({ config }: { config: Config }) {
  const [open, setOpen] = useState<string | null>(null)
  const section = SECTIONS.find((s) => s.key === open)
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">Coder · Configuration</h1>
      <RecreateNotice config={config} />
      <div className="grid grid-cols-2 gap-3">
        {SECTIONS.map((s) => {
          const attention = s.attention?.(config)
          return (
            <Card key={s.key} size="sm">
              <CardHeader>
                <CardTitle>
                  <span className="flex items-center gap-2">
                    <s.icon />
                    {s.label}
                    {attention && <Badge variant="destructive">{attention}</Badge>}
                  </span>
                </CardTitle>
                <CardDescription>{summary(s.key, config)}</CardDescription>
                <CardAction>
                  <Button size="sm" variant="outline" onClick={() => setOpen(s.key)}>
                    Open
                  </Button>
                </CardAction>
              </CardHeader>
            </Card>
          )
        })}
      </div>
      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
          {section && (
            <>
              <SheetHeader>
                <SheetTitle>{section.label}</SheetTitle>
                <SheetDescription>{section.hint}</SheetDescription>
              </SheetHeader>
              <div className="px-4 pb-4">{section.render(config)}</div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
