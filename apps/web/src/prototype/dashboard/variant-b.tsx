// PROTOTYPE, throwaway. Variant B: plan use pinned as a strip under the title, the rest split
// into tabs by question: how is it going, what did it spend, who started the work, how well.
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import type { Dashboard } from "./flow"
import {
  Approvals,
  HubChart,
  HubTiles,
  InstanceTable,
  LimitAlerts,
  NavigationTrend,
  Notices,
  NotCounted,
  OriginTable,
  PlansCard,
  PlansStrip,
  RangeBar,
  RoutinesPanel,
  Selection,
  SourcesTable,
  Tiles,
  ToolsMemory,
  TrendChart,
  TurnsTable,
} from "./parts"

export const name = "Tabs, plans strip"

function Header({ d, title }: { d: Dashboard; title: string }) {
  return (
    <div className="flex flex-col gap-3 border-b pb-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">{title}</h1>
        <RangeBar d={d} />
      </div>
      <PlansStrip d={d} />
    </div>
  )
}

export function InstanceStats({ d }: { d: Dashboard }) {
  if (!d.stats || !d.instance) return null
  const { total, buckets } = d.stats
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6 pb-72">
      <Header d={d} title={`${d.instance.name} · Stats`} />
      <Tabs defaultValue="overview">
        <TabsList variant="line">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="spend">Spend</TabsTrigger>
          <TabsTrigger value="origin">Origin</TabsTrigger>
          <TabsTrigger value="quality">Quality</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="flex flex-col gap-4 pt-4">
          <Notices d={d} />
          <Tiles total={total} />
          <TrendChart d={d} buckets={buckets} metric="turns" />
          <Selection d={d} />
          <TurnsTable d={d} limit={8} />
        </TabsContent>
        <TabsContent value="spend" className="flex flex-col gap-4 pt-4">
          <TrendChart d={d} buckets={buckets} metric="cost" className="h-48" />
          <SourcesTable total={total} />
          <TrendChart d={d} buckets={buckets} metric="subscription" className="h-48" />
          <PlansCard d={d} />
        </TabsContent>
        <TabsContent value="origin" className="flex flex-col gap-4 pt-4">
          <OriginTable d={d} total={total} />
          <Selection d={d} />
          <TurnsTable d={d} limit={8} />
          <h2 className="font-medium">Routines</h2>
          <RoutinesPanel routines={d.instance.routines} />
        </TabsContent>
        <TabsContent value="quality" className="flex flex-col gap-4 pt-4">
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Tools and memory</CardTitle>
              </CardHeader>
              <CardContent>
                <ToolsMemory total={total} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Approvals and ratings</CardTitle>
              </CardHeader>
              <CardContent>
                <Approvals total={total} />
              </CardContent>
            </Card>
          </div>
          <NavigationTrend d={d} buckets={buckets} />
        </TabsContent>
      </Tabs>
    </main>
  )
}

export function HubUsage({ d }: { d: Dashboard }) {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-4 overflow-y-auto p-6 pb-72">
      <Header d={d} title="Usage" />
      <LimitAlerts d={d} />
      <InstanceTable d={d} />
      <NotCounted d={d} />
      <HubTiles d={d} />
      <Tabs defaultValue="cost">
        <TabsList>
          <TabsTrigger value="cost">API cost</TabsTrigger>
          <TabsTrigger value="subscription">Plan runs</TabsTrigger>
          <TabsTrigger value="turns">Turns</TabsTrigger>
        </TabsList>
        <TabsContent value="cost">
          <HubChart d={d} metric="cost" />
        </TabsContent>
        <TabsContent value="subscription">
          <HubChart d={d} metric="subscription" />
        </TabsContent>
        <TabsContent value="turns">
          <HubChart d={d} metric="turns" />
        </TabsContent>
      </Tabs>
    </main>
  )
}
