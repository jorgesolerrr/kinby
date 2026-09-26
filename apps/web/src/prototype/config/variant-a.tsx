// PROTOTYPE, throwaway. Variant A: a settings page with a row of tabs, one per section.
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import type { Config } from "./flow"
import { RecreateNotice, SECTIONS } from "./parts"

export const name = "Tabs"

export function VariantA({ config }: { config: Config }) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">Coder · Configuration</h1>
      <RecreateNotice config={config} />
      <Tabs defaultValue="behavior">
        <TabsList variant="line" className="flex-wrap">
          {SECTIONS.map((s) => {
            const attention = s.attention?.(config)
            return (
              <TabsTrigger key={s.key} value={s.key}>
                <s.icon />
                {s.label}
                {attention && <Badge variant="destructive">!</Badge>}
              </TabsTrigger>
            )
          })}
        </TabsList>
        {SECTIONS.map((s) => (
          <TabsContent key={s.key} value={s.key}>
            <div className="flex flex-col gap-3 pt-4">
              <p className="text-sm text-muted-foreground">{s.hint}</p>
              {s.render(config)}
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
