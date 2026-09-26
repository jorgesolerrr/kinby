// PROTOTYPE, throwaway. Pieces the three dashboard variants compose differently.
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { AlertTriangleIcon, ClockIcon, RefreshCwIcon, XIcon } from "lucide-react"

import type { Dashboard } from "./flow"
import {
  INSTANCES,
  instanceName,
  NOW,
  SOURCE_LABEL,
  type Range,
  type RoutineSummary,
  type StatsBucket,
  type StatsSummary,
  type SubscriptionUse,
} from "./stub"

// ---- formatting ----------------------------------------------------------------------------

export const tokens = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : `${n}`
export const money = (n: number | null) => (n === null ? "—" : `$${n.toFixed(2)}`)
export const duration = (ms: number) => {
  const m = Math.round(ms / 60_000)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}
export const time = (d: Date) => `${d.toISOString().slice(11, 16)} UTC`
export const bucketLabel = (start: string, by: "day" | "week") => {
  const d = new Date(`${start}T00:00:00Z`)
  const s = d.toLocaleDateString("en", { month: "short", day: "numeric", timeZone: "UTC" })
  return by === "week" ? `wk ${s}` : s
}
export const turns = (s: StatsSummary) => s.completed + s.failed + s.interrupted

// ---- controls ------------------------------------------------------------------------------

export function RangeBar({ d }: { d: Dashboard }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ToggleGroup
        variant="outline"
        size="sm"
        value={[String(d.range)]}
        onValueChange={(v) => v[0] && d.setRange(Number(v[0]) as Range)}
      >
        <ToggleGroupItem value="7">7 days</ToggleGroupItem>
        <ToggleGroupItem value="30">30 days</ToggleGroupItem>
        <ToggleGroupItem value="90">90 days</ToggleGroupItem>
      </ToggleGroup>
      <span className="text-xs text-muted-foreground">{d.by === "week" ? "Weekly" : "Daily"} buckets, UTC</span>
      <Button size="icon-sm" variant="ghost" onClick={d.refresh} aria-label="Refresh">
        <RefreshCwIcon />
      </Button>
    </div>
  )
}

export function Selection({ d }: { d: Dashboard }) {
  if (!d.bucket && !d.origin) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      Showing
      {d.bucket && (
        <Badge variant="secondary">
          {bucketLabel(d.bucket, d.by)}
          <button onClick={() => d.setBucket(null)} aria-label="Clear bucket">
            <XIcon className="size-3" />
          </button>
        </Badge>
      )}
      {d.origin && (
        <Badge variant="secondary">
          {d.origin === "chat" ? "Chat" : d.origin}
          <button onClick={() => d.setOrigin(null)} aria-label="Clear origin">
            <XIcon className="size-3" />
          </button>
        </Badge>
      )}
    </div>
  )
}

// ---- tiles ---------------------------------------------------------------------------------

export type Metric = "turns" | "cost" | "subscription" | "navigation"

export function Tiles({
  total,
  active,
  onPick,
}: {
  total: StatsSummary
  active?: Metric
  onPick?: (m: Metric) => void
}) {
  const n = turns(total)
  const runs = total.subscriptions.reduce((a, s) => a + s.runs, 0)
  const rated = total.good_ratings + total.bad_ratings
  const tiles: { metric?: Metric; title: string; value: string; note: string }[] = [
    { metric: "turns", title: "Turns", value: `${n}`, note: `${total.failed} failed · ${total.interrupted} interrupted` },
    { metric: "cost", title: "API cost", value: money(total.cost), note: `${tokens(total.input_tokens)} in · ${tokens(total.output_tokens)} out` },
    { metric: "subscription", title: "Plan runs", value: `${runs}`, note: "Delegated to Claude and ChatGPT plans" },
    {
      metric: "navigation",
      title: "Ratings",
      value: rated ? `${Math.round((100 * total.good_ratings) / rated)}% good` : "—",
      note: `${total.good_ratings} good · ${total.bad_ratings} bad`,
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((t) => (
        <Card
          key={t.title}
          size="sm"
          className={onPick ? `cursor-pointer ${active === t.metric ? "ring-2 ring-primary" : ""}` : ""}
          onClick={() => t.metric && onPick?.(t.metric)}
        >
          <CardHeader>
            <CardDescription>{t.title}</CardDescription>
            <CardTitle className="text-2xl tabular-nums">{t.value}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">{t.note}</CardContent>
        </Card>
      ))}
    </div>
  )
}

// ---- charts --------------------------------------------------------------------------------

const TURN_CONFIG = {
  completed: { label: "Completed", color: "var(--chart-2)" },
  failed: { label: "Failed", color: "var(--destructive)" },
  interrupted: { label: "Interrupted", color: "var(--chart-4)" },
} satisfies ChartConfig
const COST_CONFIG = { cost: { label: "API cost ($)", color: "var(--chart-1)" } } satisfies ChartConfig
const SUB_CONFIG = {
  claude: { label: "Claude plan runs", color: "var(--chart-3)" },
  chatgpt: { label: "ChatGPT plan runs", color: "var(--chart-5)" },
} satisfies ChartConfig
const NAV_CONFIG = {
  reads: { label: "Reads before first write", color: "var(--chart-1)" },
} satisfies ChartConfig

function subRuns(s: StatsSummary, source: SubscriptionUse["usage_source"]) {
  return s.subscriptions.find((x) => x.usage_source === source)?.runs ?? 0
}

export function TrendChart({
  d,
  buckets,
  metric,
  className = "h-64",
}: {
  d: Dashboard
  buckets: StatsBucket[]
  metric: Metric
  className?: string
}) {
  const data = buckets.map((b) => ({
    start: b.start,
    completed: b.completed,
    failed: b.failed,
    interrupted: b.interrupted,
    cost: b.cost === null ? 0 : Number(b.cost.toFixed(2)),
    claude: subRuns(b, "claude-subscription"),
    chatgpt: subRuns(b, "chatgpt-subscription"),
    reads: b.navigation.read_calls === null ? null : Number(b.navigation.read_calls.toFixed(1)),
  }))
  // oxlint-disable-next-line typescript/no-explicit-any
  const pick = (s: any) => s?.activeLabel && d.setBucket(String(s.activeLabel))
  const axis = (
    <>
      <CartesianGrid vertical={false} />
      <XAxis dataKey="start" tickLine={false} axisLine={false} tickFormatter={(v) => bucketLabel(v, d.by)} minTickGap={16} />
      <YAxis tickLine={false} axisLine={false} width={36} />
      <ChartTooltip content={<ChartTooltipContent labelFormatter={(v) => bucketLabel(String(v), d.by)} />} />
    </>
  )
  if (metric === "navigation")
    return (
      <ChartContainer config={NAV_CONFIG} className={`w-full ${className}`}>
        <LineChart data={data} onClick={pick}>
          {axis}
          <Line dataKey="reads" stroke="var(--color-reads)" strokeWidth={2} dot={false} connectNulls />
        </LineChart>
      </ChartContainer>
    )
  const config = metric === "turns" ? TURN_CONFIG : metric === "cost" ? COST_CONFIG : SUB_CONFIG
  const keys = Object.keys(config)
  return (
    <ChartContainer config={config} className={`w-full ${className}`}>
      <BarChart data={data} onClick={pick} className="cursor-pointer">
        {axis}
        {keys.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {keys.map((k) => (
          <Bar key={k} dataKey={k} stackId="a" fill={`var(--color-${k})`} fillOpacity={d.bucket ? 0.5 : 1} />
        ))}
      </BarChart>
    </ChartContainer>
  )
}

export function NavigationTrend({ d, buckets }: { d: Dashboard; buckets: StatsBucket[] }) {
  if (!buckets.some((b) => b.navigation.turns > 0)) return null
  const first = buckets.find((b) => b.navigation.read_calls !== null)?.navigation.read_calls
  const last = [...buckets].reverse().find((b) => b.navigation.read_calls !== null)?.navigation.read_calls
  return (
    <Card>
      <CardHeader>
        <CardTitle>Navigation</CardTitle>
        <CardDescription>
          Mean reads before the first write, per bucket. Falling means memory is teaching it the workspace.
        </CardDescription>
        {first != null && last != null && (
          <CardAction>
            <Badge variant="outline">
              {first.toFixed(1)} → {last.toFixed(1)}
            </Badge>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        <TrendChart d={d} buckets={buckets} metric="navigation" className="h-40" />
      </CardContent>
    </Card>
  )
}

// ---- plans ---------------------------------------------------------------------------------

export function LimitAlerts({ d }: { d: Dashboard }) {
  return d.limits.map((l) => (
    <Alert key={l.usage_source} variant="destructive">
      <AlertTriangleIcon />
      <AlertTitle>
        {SOURCE_LABEL[l.usage_source]} limited until {time(l.resets_at)}
      </AlertTitle>
      <AlertDescription>
        A delegated run hit the plan's limit. Runs on this plan fail until the window resets.
      </AlertDescription>
    </Alert>
  ))
}

export function PlansCard({ d }: { d: Dashboard }) {
  if (!d.plans) return null
  const { fiveHours, sevenDays } = d.plans
  return (
    <Card>
      <CardHeader>
        <CardTitle>Plans</CardTitle>
        <CardDescription>Rolling windows, whatever the range above. Counts only, never what's left.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <LimitAlerts d={d} />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Plan</TableHead>
              <TableHead className="text-right">Last 5 hours</TableHead>
              <TableHead className="text-right">Last 7 days</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {fiveHours.map((five, i) => {
              const seven = sevenDays[i]
              return (
                <TableRow key={five.usage_source}>
                  <TableCell className="font-medium">{SOURCE_LABEL[five.usage_source]}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {five.runs} runs · {tokens(five.input_tokens + five.output_tokens)} · {duration(five.duration_ms)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {seven.runs} runs · {tokens(seven.input_tokens + seven.output_tokens)} · {duration(seven.duration_ms)}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export function PlansStrip({ d }: { d: Dashboard }) {
  if (!d.plans) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      {d.plans.fiveHours.map((five, i) => {
        const limit = d.limits.find((l) => l.usage_source === five.usage_source)
        return (
          <Badge key={five.usage_source} variant={limit ? "destructive" : "outline"} className="h-7 gap-1.5 px-2.5">
            {limit ? <AlertTriangleIcon /> : <ClockIcon />}
            {SOURCE_LABEL[five.usage_source]}: {five.runs} runs in 5h · {d.plans?.sevenDays[i].runs} in 7d
            {limit && ` · limited until ${time(limit.resets_at)}`}
          </Badge>
        )
      })}
    </div>
  )
}

// ---- usage by source -----------------------------------------------------------------------

export function SourcesTable({ total }: { total: StatsSummary }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Source</TableHead>
          <TableHead className="text-right">Runs</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
          <TableHead className="text-right">Time</TableHead>
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell className="font-medium">API</TableCell>
          <TableCell className="text-right text-muted-foreground">{turns(total)} turns</TableCell>
          <TableCell className="text-right tabular-nums">{tokens(total.input_tokens + total.output_tokens)}</TableCell>
          <TableCell className="text-right text-muted-foreground">—</TableCell>
          <TableCell className="text-right tabular-nums">{money(total.cost)}</TableCell>
        </TableRow>
        {total.subscriptions.map((s) => (
          <TableRow key={s.usage_source}>
            <TableCell className="font-medium">{SOURCE_LABEL[s.usage_source]}</TableCell>
            <TableCell className="text-right tabular-nums">{s.runs}</TableCell>
            <TableCell className="text-right tabular-nums">{tokens(s.input_tokens + s.output_tokens)}</TableCell>
            <TableCell className="text-right tabular-nums">{duration(s.duration_ms)}</TableCell>
            <TableCell className="text-right text-muted-foreground">not priced</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ---- by origin -----------------------------------------------------------------------------

export function OriginTable({ d, total }: { d: Dashboard; total: StatsSummary }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Origin</TableHead>
          <TableHead className="text-right">Turns</TableHead>
          <TableHead className="text-right">Failed</TableHead>
          <TableHead className="text-right">API cost</TableHead>
          <TableHead className="text-right">Plan runs</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {total.by_origin.map((o) => (
          <TableRow
            key={o.origin}
            className="cursor-pointer"
            data-state={d.origin === o.origin ? "selected" : undefined}
            onClick={() => d.setOrigin(d.origin === o.origin ? null : o.origin)}
          >
            <TableCell className="font-medium">{o.origin === "chat" ? "Chat" : o.origin}</TableCell>
            <TableCell className="text-right tabular-nums">{o.turns}</TableCell>
            <TableCell className="text-right tabular-nums">{o.failed || "—"}</TableCell>
            <TableCell className="text-right tabular-nums">{money(o.cost)}</TableCell>
            <TableCell className="text-right tabular-nums">{o.subscription_runs || "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ---- tools, memory, approvals ---------------------------------------------------------------

export function ToolsMemory({ total }: { total: StatsSummary }) {
  const top = Object.entries(total.tool_calls)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
  const max = top[0]?.[1] ?? 1
  const m = total.memory_calls
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="flex flex-col gap-1.5">
        <div className="text-sm font-medium">Most-called tools</div>
        {top.map(([name, n]) => (
          <div key={name} className="flex items-center gap-2 text-sm">
            <span className="w-24 truncate font-mono text-xs">{name}</span>
            <div className="h-2 flex-1 rounded-full bg-muted">
              <div className="h-2 rounded-full bg-primary" style={{ width: `${(100 * n) / max}%` }} />
            </div>
            <span className="w-12 text-right tabular-nums">{n}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1.5 text-sm">
        <div className="font-medium">Memory</div>
        <Row label="Searches" value={m.search} />
        <Row label="Opens" value={m.open} />
        <Row label="Remembered" value={m.remember} />
        <Row label="Forgotten" value={m.forget} />
        <Row label="Turns without memory" value={total.turns_without_memory} />
      </div>
    </div>
  )
}

export function Approvals({ total }: { total: StatsSummary }) {
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <Row label="Approvals asked" value={total.approvals_requested} />
      <Row label="Denied" value={total.denies} />
      <Row label="Good ratings" value={total.good_ratings} />
      <Row label="Bad ratings" value={total.bad_ratings} />
    </div>
  )
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  )
}

// ---- routines, notices ---------------------------------------------------------------------

export function RoutinesPanel({ routines }: { routines: RoutineSummary[] }) {
  if (routines.length === 0) return <p className="text-sm text-muted-foreground">No routines.</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Routine</TableHead>
          <TableHead>Last run</TableHead>
          <TableHead className="text-right">Failures in a row</TableHead>
          <TableHead className="text-right">Next</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {routines.map((r) => (
          <TableRow key={r.name}>
            <TableCell className="font-medium">{r.name}</TableCell>
            <TableCell>
              {r.last_run ? (
                <Badge variant={r.last_run.outcome === "failed" ? "destructive" : "outline"}>{r.last_run.outcome}</Badge>
              ) : (
                "—"
              )}
            </TableCell>
            <TableCell className="text-right tabular-nums">{r.failure_count || "—"}</TableCell>
            <TableCell className="text-right">{r.next_run ? time(r.next_run) : r.schedule ? "—" : "on signal"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function Notices({ d }: { d: Dashboard }) {
  if (!d.stats) return null
  const { unpriced_models, mismatches } = d.stats
  if (!unpriced_models.length && !mismatches) return null
  return (
    <Alert>
      <AlertTriangleIcon />
      <AlertTitle>Some numbers are incomplete</AlertTitle>
      <AlertDescription>
        {unpriced_models.length > 0 && <p>No price for {unpriced_models.join(", ")}, so its turns count no cost.</p>}
        {mismatches > 0 && <p>{mismatches} turn's model calls don't add up to its token usage.</p>}
      </AlertDescription>
    </Alert>
  )
}

// ---- drill-down ----------------------------------------------------------------------------

export function TurnsTable({ d, limit = 12 }: { d: Dashboard; limit?: number }) {
  if (!d.stats) return null
  const rows = d.stats.records
    .filter((t) => !d.bucket || bucketOf(t.closed_at, d.by) === d.bucket)
    .filter((t) => !d.origin || (t.origin.kind === "user" ? "chat" : t.origin.name) === d.origin)
    .sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0))
  return (
    <div className="flex flex-col gap-2">
      <div className="text-sm text-muted-foreground">
        {rows.length} turns, most expensive first{rows.length > limit && `, top ${limit} shown`}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Turn</TableHead>
            <TableHead>Origin</TableHead>
            <TableHead>Closed</TableHead>
            <TableHead>Outcome</TableHead>
            <TableHead className="text-right">API cost</TableHead>
            <TableHead>Plan runs</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, limit).map((t) => (
            <TableRow key={t.turn_id}>
              <TableCell>
                <a className="font-medium underline-offset-4 hover:underline" href="#">
                  {t.thread_title}
                </a>
                {t.rating && <span className="ml-1.5">{t.rating === "good" ? "👍" : "👎"}</span>}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {t.origin.kind === "user" ? "Chat" : `${t.origin.name} (${t.origin.trigger})`}
              </TableCell>
              <TableCell className="tabular-nums">{t.closed_at.toISOString().slice(5, 16).replace("T", " ")}</TableCell>
              <TableCell>
                <Badge variant={t.closing_kind === "completed" ? "outline" : "destructive"}>{t.closing_kind}</Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{money(t.cost)}</TableCell>
              <TableCell className="text-xs">
                {t.delegated_runs.map((r, i) => (
                  <Badge key={i} variant={r.outcome === "limited" ? "destructive" : "secondary"} className="mr-1">
                    {r.client} {duration(r.duration_ms)}
                  </Badge>
                ))}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function bucketOf(date: Date, by: "day" | "week") {
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  if (by === "week") day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7))
  return day.toISOString().slice(0, 10)
}

// ---- hub -----------------------------------------------------------------------------------

const INSTANCE_COLORS = ["var(--chart-1)", "var(--chart-3)", "var(--chart-5)"]

export function HubChart({
  d,
  metric,
  className = "h-64",
}: {
  d: Dashboard
  metric: "cost" | "turns" | "subscription"
  className?: string
}) {
  if (!d.hub) return null
  const ids = Object.keys(d.hub.buckets)
  const config = Object.fromEntries(
    ids.map((id, i) => [id, { label: instanceName(id), color: INSTANCE_COLORS[i % 3] }]),
  ) satisfies ChartConfig
  const starts = d.hub.buckets[ids[0]]?.map((b) => b.start) ?? []
  const value = (b: StatsBucket) =>
    metric === "cost" ? Number((b.cost ?? 0).toFixed(2)) : metric === "turns" ? turns(b) : b.subscriptions.reduce((a, s) => a + s.runs, 0)
  const data = starts.map((start, j) => ({
    start,
    ...Object.fromEntries(ids.map((id) => [id, value(d.hub!.buckets[id][j])])),
  }))
  return (
    <ChartContainer config={config} className={`w-full ${className}`}>
      {/* oxlint-disable-next-line typescript/no-explicit-any */}
      <BarChart data={data} onClick={(s: any) => s?.activeLabel && d.setBucket(String(s.activeLabel))}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="start" tickLine={false} axisLine={false} tickFormatter={(v) => bucketLabel(v, d.by)} minTickGap={16} />
        <YAxis tickLine={false} axisLine={false} width={36} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(v) => bucketLabel(String(v), d.by)} />} />
        <ChartLegend content={<ChartLegendContent />} />
        {ids.map((id) => (
          <Bar key={id} dataKey={id} stackId="a" fill={`var(--color-${id})`} />
        ))}
      </BarChart>
    </ChartContainer>
  )
}

export function hubTotal(d: Dashboard) {
  if (!d.hub) return null
  const all = Object.values(d.hub.totals)
  return {
    turns: all.reduce((a, t) => a + turns(t), 0),
    failed: all.reduce((a, t) => a + t.failed, 0),
    ...d.hub.api,
    runs: d.hub.subscriptions.reduce((a, s) => a + s.runs, 0),
  }
}

export function HubTiles({ d }: { d: Dashboard }) {
  const t = hubTotal(d)
  if (!t || !d.hub) return null
  const tiles = [
    { title: "Turns", value: `${t.turns}`, note: `${t.failed} failed` },
    { title: "API cost", value: money(t.cost), note: `${tokens(t.input_tokens)} in · ${tokens(t.output_tokens)} out` },
    ...d.hub.subscriptions.map((s) => ({
      title: SOURCE_LABEL[s.usage_source],
      value: `${s.runs} runs`,
      note: `${tokens(s.input_tokens + s.output_tokens)} · ${duration(s.duration_ms)}`,
    })),
  ]
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((x) => (
        <Card key={x.title} size="sm">
          <CardHeader>
            <CardDescription>{x.title}</CardDescription>
            <CardTitle className="text-2xl tabular-nums">{x.value}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">{x.note}</CardContent>
        </Card>
      ))}
    </div>
  )
}

export function InstanceTable({ d, bucket }: { d: Dashboard; bucket?: string | null }) {
  if (!d.hub) return null
  const hub = d.hub
  const totalFor = (id: string): StatsSummary | undefined =>
    bucket ? hub.buckets[id]?.find((b) => b.start === bucket) : hub.totals[id]
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Instance</TableHead>
          <TableHead className="text-right">Turns</TableHead>
          <TableHead className="text-right">API cost</TableHead>
          <TableHead className="text-right">Claude runs</TableHead>
          <TableHead className="text-right">ChatGPT runs</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {INSTANCES.map((i) => {
          const t = totalFor(i.id)
          const reason = hub.skipped.includes(i.id) ? "stopped" : hub.unreachable.includes(i.id) ? "didn't answer" : null
          return (
            <TableRow key={i.id} className={reason ? "text-muted-foreground" : ""}>
              <TableCell className="font-medium">
                {i.name}
                {i.package && <span className="ml-1.5 text-xs text-muted-foreground">{i.package}</span>}
              </TableCell>
              {reason || !t ? (
                <TableCell colSpan={4} className="text-right">
                  Not counted: {reason}
                </TableCell>
              ) : (
                <>
                  <TableCell className="text-right tabular-nums">{turns(t)}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(t.cost)}</TableCell>
                  <TableCell className="text-right tabular-nums">{subRuns(t, "claude-subscription") || "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{subRuns(t, "chatgpt-subscription") || "—"}</TableCell>
                </>
              )}
              <TableCell className="text-right">
                {!reason && (
                  <Button size="sm" variant="ghost" onClick={() => d.open({ kind: "stats", instanceId: i.id })}>
                    Stats
                  </Button>
                )}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

export function NotCounted({ d }: { d: Dashboard }) {
  if (!d.hub || (!d.hub.skipped.length && !d.hub.unreachable.length)) return null
  return (
    <p className="text-xs text-muted-foreground">
      Totals leave out {[...d.hub.skipped.map((id) => `${instanceName(id)} (stopped)`), ...d.hub.unreachable.map((id) => `${instanceName(id)} (didn't answer)`)].join(", ")}.
    </p>
  )
}

// ---- state panel ---------------------------------------------------------------------------

export function StatePanel({ d }: { d: Dashboard }) {
  return (
    <details className="fixed right-4 bottom-4 z-40 w-[28rem] max-w-[calc(100vw-2rem)] rounded-lg border bg-popover p-3 text-xs shadow-lg" open>
      <summary className="cursor-pointer font-medium">Prototype state · now is {NOW.toISOString().slice(0, 16)}Z</summary>
      <pre className="mt-2 whitespace-pre-wrap text-muted-foreground">
        {JSON.stringify(
          { page: d.page, range: d.range, by: d.by, bucket: d.bucket, origin: d.origin },
          null,
          0,
        )}
      </pre>
      <div className="mt-2 font-medium">Contract calls (newest first)</div>
      <ol className="mt-1 max-h-40 overflow-y-auto font-mono text-[11px] leading-5">
        {d.calls.map((c, i) => (
          <li key={i} className="truncate" title={c}>
            {c}
          </li>
        ))}
      </ol>
    </details>
  )
}
