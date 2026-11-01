// PROTOTYPE: the small pieces both Settings variants draw the same way. Layout stays per variant.
import { BellIcon, MessageSquareIcon, SendIcon } from "lucide-react"
import { useId, useState } from "react"
import { Bar, BarChart, XAxis } from "recharts"

import { Field, FieldLabel } from "@/components/ui/field"
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { isTheme, useTheme } from "@/lib/theme"
import { cn } from "cn"
import { theme } from "@/theme"

import { DAILY, PLANS, type Removed, type Section, TOTALS, USAGE_ROWS } from "./mock"

export interface SettingsProps {
  section: Section | undefined
  onSection: (section: Section) => void
  onClose: () => void
  empty: boolean
}

/** Keep the dialog open when the press lands on the prototype's own floating bar. */
export function fromBar(event: Event | undefined) {
  return event?.target instanceof Element && event.target.closest("[data-proto-bar]") !== null
}

/** Pretend the hub takes a moment, then run `done`. */
export function useBusy(ms = 900) {
  const [busy, setBusy] = useState<string>()
  return {
    busy,
    run: (key: string, done: () => void) => {
      setBusy(key)
      setTimeout(() => {
        setBusy(undefined)
        done()
      }, ms)
    },
  }
}

export function ThemeControl({ labelledBy }: { labelledBy: string }) {
  const current = useTheme(theme)
  return (
    <ToggleGroup
      aria-labelledby={labelledBy}
      size="sm"
      variant="outline"
      value={[current]}
      onValueChange={([picked]) => {
        if (picked !== undefined && isTheme(picked)) theme.setTheme(picked)
      }}
    >
      <ToggleGroupItem value="light">Light</ToggleGroupItem>
      <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
      <ToggleGroupItem value="system">System</ToggleGroupItem>
    </ToggleGroup>
  )
}

export function RangeToggle() {
  const [range, setRange] = useState("7")
  return (
    <ToggleGroup
      aria-label="Range"
      size="sm"
      variant="outline"
      value={[range]}
      onValueChange={([picked]) => picked !== undefined && setRange(picked)}
    >
      <ToggleGroupItem value="7">7 days</ToggleGroupItem>
      <ToggleGroupItem value="30">30 days</ToggleGroupItem>
      <ToggleGroupItem value="90">90 days</ToggleGroupItem>
    </ToggleGroup>
  )
}

export function PlanMeters({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {PLANS.map((plan) => (
        <div key={plan.label} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium">{plan.label}</span>
            <span className="text-muted-foreground tabular-nums">
              {plan.used}% · {plan.resets}
            </span>
          </div>
          <Progress value={plan.used} aria-label={plan.label} />
        </div>
      ))}
    </div>
  )
}

export function Totals({ className }: { className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-3 lg:grid-cols-4", className)}>
      {TOTALS.map((tile) => (
        <div key={tile.title} className="flex flex-col gap-0.5 rounded-xl border p-3">
          <span className="text-xs text-muted-foreground">{tile.title}</span>
          <span className="font-heading text-xl font-semibold tabular-nums">{tile.value}</span>
          <span className="truncate text-xs text-muted-foreground">{tile.note}</span>
        </div>
      ))}
    </div>
  )
}

const CHART: ChartConfig = {
  kin: { label: "Kin", color: "var(--chart-1)" },
  scout: { label: "Scout", color: "var(--chart-2)" },
  coder: { label: "cec-coder", color: "var(--chart-3)" },
}

export function TurnsChart({ className }: { className?: string }) {
  return (
    <ChartContainer config={CHART} className={cn("aspect-auto h-40 w-full", className)}>
      <BarChart data={DAILY} accessibilityLayer>
        <XAxis dataKey="day" tickLine={false} axisLine={false} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="kin" stackId="a" fill="var(--color-kin)" />
        <Bar dataKey="scout" stackId="a" fill="var(--color-scout)" />
        <Bar dataKey="coder" stackId="a" fill="var(--color-coder)" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}

/** The per-instance table, plan runs summed so it fits the dialog's width. */
export function UsageTable() {
  return (
    <Table aria-label="Usage per instance">
      <TableHeader>
        <TableRow>
          <TableHead>Instance</TableHead>
          <TableHead className="text-right">Turns</TableHead>
          <TableHead className="text-right">API cost</TableHead>
          <TableHead className="text-right">Plan runs</TableHead>
          <TableHead className="text-right">Factory</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {USAGE_ROWS.map((row) => (
          <TableRow key={row.instance}>
            <TableCell className="font-medium">{row.instance}</TableCell>
            {row.notCounted !== undefined ? (
              <TableCell colSpan={4} className="text-right text-muted-foreground">
                Not counted: {row.notCounted}
              </TableCell>
            ) : (
              <>
                <TableCell className="text-right tabular-nums">{row.turns}</TableCell>
                <TableCell className="text-right tabular-nums">{row.cost}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {(row.claude ?? 0) + (row.chatgpt ?? 0)}
                </TableCell>
                <TableCell className="text-right tabular-nums">{row.factory}</TableCell>
              </>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function KindIcon({ kind, className }: { kind: string; className?: string }) {
  const Icon = kind === "telegram" ? SendIcon : kind === "discord" ? MessageSquareIcon : BellIcon
  return <Icon aria-hidden className={cn("size-4", className)} />
}

export const kindLabel = (kind: string) =>
  ({ telegram: "Telegram", discord: "Discord", ntfy: "ntfy" })[kind] ?? kind

/** What a permanent deletion takes, and the name typed to confirm it. */
export function DeletionPreview({
  instance,
  typed,
  onTyped,
}: {
  instance: Removed
  typed: string
  onTyped: (typed: string) => void
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-4">
      <div className="flex max-h-56 flex-col gap-3 overflow-y-auto text-sm">
        <Targets label="Directories" targets={instance.directories} />
        <Targets label="Volumes" targets={instance.volumes} />
        {instance.retained.length > 0 && (
          <Targets
            label="Stays on the Docker host"
            note="The hub can't see these binds, so it leaves them where they are."
            targets={instance.retained}
          />
        )}
      </div>
      <Field>
        <FieldLabel htmlFor={id}>Type {instance.name} to confirm</FieldLabel>
        <Input
          id={id}
          autoComplete="off"
          value={typed}
          onChange={(event) => onTyped(event.target.value)}
        />
      </Field>
    </div>
  )
}

function Targets({ label, note, targets }: { label: string; note?: string; targets: string[] }) {
  return (
    <section className="flex flex-col gap-1">
      <h4 className="font-medium">{label}</h4>
      {note !== undefined && <p className="text-muted-foreground">{note}</p>}
      <ul className="font-mono text-xs break-all text-muted-foreground">
        {targets.map((target) => (
          <li key={target}>{target}</li>
        ))}
      </ul>
    </section>
  )
}
