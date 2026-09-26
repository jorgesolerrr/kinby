// PROTOTYPE, throwaway. Variant C: the chart is the page. The tiles pick what it plots, a bar
// picks the bucket, and an inspector on the right explains the selection (or the whole range).
import { useState } from "react"

import { Separator } from "@/components/ui/separator"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import type { Dashboard } from "./flow"
import {
  Approvals,
  bucketLabel,
  HubChart,
  HubTiles,
  InstanceTable,
  LimitAlerts,
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
  type Metric,
} from "./parts"

export const name = "Chart and inspector"

function Inspector({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <aside className="flex w-full flex-col gap-4 overflow-y-auto border-l p-4 pb-72 lg:w-[26rem] lg:shrink-0">
      <h2 className="font-semibold">{title}</h2>
      {children}
    </aside>
  )
}

export function InstanceStats({ d }: { d: Dashboard }) {
  const [metric, setMetric] = useState<Metric>("turns")
  if (!d.stats || !d.instance) return null
  const { total, buckets } = d.stats
  const selected = d.bucket ? buckets.find((b) => b.start === d.bucket) : undefined
  const scope = selected ?? total
  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6 pb-72">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">{d.instance.name} · Stats</h1>
          <RangeBar d={d} />
        </div>
        <LimitAlerts d={d} />
        <Tiles total={total} active={metric} onPick={setMetric} />
        <TrendChart d={d} buckets={buckets} metric={metric} className="h-80" />
        <Notices d={d} />
        <Separator />
        <div className="grid gap-6 lg:grid-cols-2">
          <ToolsMemory total={total} />
          <Approvals total={total} />
        </div>
        <Separator />
        <h2 className="font-medium">Routines</h2>
        <RoutinesPanel routines={d.instance.routines} />
      </main>
      <Inspector title={selected ? bucketLabel(selected.start, d.by) : `Last ${d.range} days`}>
        <Selection d={d} />
        <OriginTable d={d} total={scope} />
        <SourcesTable total={scope} />
        <TurnsTable d={d} limit={6} />
        <PlansCard d={d} />
      </Inspector>
    </div>
  )
}

export function HubUsage({ d }: { d: Dashboard }) {
  const [metric, setMetric] = useState<"cost" | "turns" | "subscription">("cost")
  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6 pb-72">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">Usage</h1>
          <RangeBar d={d} />
        </div>
        <HubTiles d={d} />
        <ToggleGroup
          variant="outline"
          size="sm"
          value={[metric]}
          onValueChange={(v) => v[0] && setMetric(v[0] as typeof metric)}
        >
          <ToggleGroupItem value="cost">API cost</ToggleGroupItem>
          <ToggleGroupItem value="subscription">Plan runs</ToggleGroupItem>
          <ToggleGroupItem value="turns">Turns</ToggleGroupItem>
        </ToggleGroup>
        <HubChart d={d} metric={metric} className="h-80" />
        <NotCounted d={d} />
      </main>
      <Inspector title={d.bucket ? bucketLabel(d.bucket, d.by) : `Last ${d.range} days`}>
        <Selection d={d} />
        <InstanceTable d={d} bucket={d.bucket} />
        <PlansCard d={d} />
      </Inspector>
    </div>
  )
}
