// PROTOTYPE, throwaway. Variant A: one long report. Every panel in the agreed order, top to
// bottom; clicking a bar or an origin narrows the turns table at the end.
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

import type { Dashboard } from "./flow"
import {
  Approvals,
  HubChart,
  HubTiles,
  InstanceTable,
  NavigationTrend,
  Notices,
  NotCounted,
  OriginTable,
  PlansCard,
  RangeBar,
  RoutinesPanel,
  Selection,
  SourcesTable,
  Tiles,
  ToolsMemory,
  TrendChart,
  TurnsTable,
} from "./parts"

export const name = "One long report"

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function InstanceStats({ d }: { d: Dashboard }) {
  if (!d.stats || !d.instance) return null
  const { total, buckets } = d.stats
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6 pb-72">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">{d.instance.name} · Stats</h1>
        <RangeBar d={d} />
      </div>
      <Notices d={d} />
      <Tiles total={total} />
      <Section title="Turns and API cost" description="Click a bar to see its turns below.">
        <TrendChart d={d} buckets={buckets} metric="turns" />
        <TrendChart d={d} buckets={buckets} metric="cost" className="h-32" />
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Usage by source">
          <SourcesTable total={total} />
        </Section>
        <PlansCard d={d} />
      </div>
      <Section title="By origin" description="Chat and each routine. Click a row to filter the turns.">
        <OriginTable d={d} total={total} />
      </Section>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Section title="Tools and memory">
            <ToolsMemory total={total} />
          </Section>
        </div>
        <Section title="Approvals and ratings">
          <Approvals total={total} />
        </Section>
      </div>
      <NavigationTrend d={d} buckets={buckets} />
      <Section title="Routines">
        <RoutinesPanel routines={d.instance.routines} />
      </Section>
      <Section title="Turns">
        <div className="flex flex-col gap-2">
          <Selection d={d} />
          <TurnsTable d={d} />
        </div>
      </Section>
    </main>
  )
}

export function HubUsage({ d }: { d: Dashboard }) {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6 pb-72">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Usage</h1>
        <RangeBar d={d} />
      </div>
      <HubTiles d={d} />
      <NotCounted d={d} />
      <PlansCard d={d} />
      <Section title="API cost by instance">
        <HubChart d={d} metric="cost" />
      </Section>
      <Section title="Instances">
        <InstanceTable d={d} />
      </Section>
    </main>
  )
}
