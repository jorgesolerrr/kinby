import type {
  Binding,
  ListBinding,
  TextProp,
  View,
  ViewCard,
  ViewChart,
  ViewChecklist,
  ViewComponent,
  ViewComponentId,
  ViewKanban,
  ViewLink,
  ViewStat,
  ViewTable,
} from "@kinby/contract"
import { CircleXIcon } from "lucide-react"
import { Component, type ReactNode, useId } from "react"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  XAxis,
  YAxis,
} from "recharts"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldLabel } from "@/components/ui/field"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { reason } from "@/lib/operation"

/** What a link in a view may open. Anything else, such as a script, shows as text. */
const LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"])

/** The theme's chart colors, taken in turn by a chart's series. */
const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
]

const CHARTS = { line: LineChart, bar: BarChart, area: AreaChart }

/** The components by id, and the data their bindings read: the view's, or one list item. */
interface Scope {
  components: ReadonlyMap<ViewComponentId, ViewComponent>
  data: unknown
}

/**
 * A view, rendered from its root with each catalogue component as its shadcn counterpart and each
 * binding read from the view's data. A component that is unknown or fails to render shows an inline
 * error in its place, and the rest of the view still shows.
 */
export function ViewRenderer({ view }: { view: Pick<View, "spec" | "data"> }) {
  const components = new Map(view.spec.components.map((component) => [component.id, component]))
  return <Rendered id="root" scope={{ components, data: view.data }} />
}

function Rendered({ id, scope }: { id: ViewComponentId; scope: Scope }) {
  const component = scope.components.get(id)
  return (
    <ComponentBoundary>
      {component === undefined ? (
        <ComponentError>The view has no component "{id}".</ComponentError>
      ) : (
        <Shown component={component} scope={scope} />
      )}
    </ComponentBoundary>
  )
}

function Shown({ component, scope }: { component: ViewComponent; scope: Scope }) {
  switch (component.component) {
    case "Card":
      return <ViewCardShown card={component} scope={scope} />
    case "Row":
      return (
        <div className="flex flex-wrap gap-3 *:min-w-48 *:flex-1">
          <Children named={component.children ?? []} scope={scope} />
        </div>
      )
    case "Text":
      return <p>{text(component.text, scope)}</p>
    case "Link":
      return <ViewLinkShown link={component} scope={scope} />
    case "Table":
      return <ViewTableShown table={component} scope={scope} />
    case "Badge":
      return <Badge variant="secondary">{text(component.label, scope)}</Badge>
    case "Kanban":
      return <ViewKanbanShown kanban={component} scope={scope} />
    case "Stat":
      return <ViewStatShown stat={component} scope={scope} />
    case "Chart":
      return <ViewChartShown chart={component} scope={scope} />
    case "Checklist":
      return <ViewChecklistShown checklist={component} scope={scope} />
  }
  return <Unknown component={component} />
}

function ViewCardShown({ card, scope }: { card: ViewCard; scope: Scope }) {
  const title = text(card.title, scope)
  const description = text(card.description, scope)
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <div className="flex flex-col gap-3">
          <Children named={card.children ?? []} scope={scope} />
        </div>
      </CardContent>
    </Card>
  )
}

/** Children named by id, or a template repeated once per item of an array in the data. */
function Children({ named, scope }: { named: ViewComponentId[] | ListBinding; scope: Scope }) {
  if (Array.isArray(named)) {
    return named.map((id, index) => <Rendered key={`${index} ${id}`} id={id} scope={scope} />)
  }
  return items(named, scope).map((item, index) => (
    <Rendered key={index} id={named.template} scope={{ ...scope, data: item }} />
  ))
}

function ViewTableShown({ table, scope }: { table: ViewTable; scope: Scope }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {table.columns.map((column, index) => (
            <TableHead key={index}>{text(column.header, scope)}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items(table.rows, scope).map((row, index) => (
          <TableRow key={index}>
            {table.columns.map((column, cell) => (
              <TableCell key={cell} className="whitespace-normal">
                <Rendered id={column.cell} scope={{ ...scope, data: row }} />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function ViewKanbanShown({ kanban, scope }: { kanban: ViewKanban; scope: Scope }) {
  return (
    <div className="flex gap-3 overflow-x-auto">
      {items(kanban.columns, scope).map((column, index) => {
        const inColumn = { ...scope, data: column }
        const title = text(kanban.title, inColumn)
        return (
          <section
            key={index}
            aria-label={title}
            className="flex w-64 shrink-0 flex-col gap-2 rounded-lg bg-muted p-2"
          >
            <h3 className="px-1 font-medium">{title}</h3>
            <Children named={kanban.cards} scope={inColumn} />
          </section>
        )
      })}
    </div>
  )
}

function ViewStatShown({ stat, scope }: { stat: ViewStat; scope: Scope }) {
  const label = text(stat.label, scope)
  const description = text(stat.description, scope)
  return (
    <section aria-label={label}>
      <Card size="sm">
        <CardHeader>
          <CardDescription>{label}</CardDescription>
          <CardTitle>
            <p className="text-2xl font-semibold tabular-nums">{text(stat.value, scope)}</p>
          </CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      </Card>
    </section>
  )
}

/**
 * A chart whose series are keyed by their place, not by anything in the data: the chart writes
 * its keys into a stylesheet.
 */
function ViewChartShown({ chart, scope }: { chart: ViewChart; scope: Scope }) {
  const keys = chart.series.map((_, index) => `series${index}`)
  const config: ChartConfig = Object.fromEntries(
    chart.series.map((series, index) => [
      keys[index],
      { label: series.label, color: SERIES_COLORS[index % SERIES_COLORS.length] },
    ]),
  )
  const points = items(chart.points, scope).map((point) => {
    const inPoint = { ...scope, data: point }
    return Object.fromEntries([
      ["x", text(chart.x, inPoint)],
      ...chart.series.map((series, index) => [keys[index], number(series.value, inPoint)]),
    ])
  })
  const Chart = CHARTS[chart.kind]
  return (
    <ChartContainer config={config} className="aspect-auto h-48 w-full">
      <Chart data={points} accessibilityLayer>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="x" tickLine={false} axisLine={false} minTickGap={16} />
        <YAxis tickLine={false} axisLine={false} width={40} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        {keys.map((key) => {
          const color = `var(--color-${key})`
          switch (chart.kind) {
            case "line":
              return <Line key={key} dataKey={key} stroke={color} dot={false} />
            case "bar":
              return <Bar key={key} dataKey={key} fill={color} />
            case "area":
              return <Area key={key} dataKey={key} stroke={color} fill={color} />
          }
        })}
      </Chart>
    </ChartContainer>
  )
}

function ViewChecklistShown({ checklist, scope }: { checklist: ViewChecklist; scope: Scope }) {
  const id = useId()
  return (
    <ul className="flex flex-col gap-2">
      {items(checklist.items, scope).map((item, index) => {
        const inItem = { ...scope, data: item }
        return (
          <li key={index}>
            <Field orientation="horizontal">
              <Checkbox id={`${id}-${index}`} checked={flag(checklist.done, inItem)} readOnly />
              <FieldLabel htmlFor={`${id}-${index}`}>{text(checklist.label, inItem)}</FieldLabel>
            </Field>
          </li>
        )
      })}
    </ul>
  )
}

function ViewLinkShown({ link, scope }: { link: ViewLink; scope: Scope }) {
  const label = text(link.label, scope)
  const href = text(link.href, scope)
  if (!opens(href)) return <span>{label}</span>
  return (
    <a className="font-medium text-primary underline" href={href} rel="noreferrer" target="_blank">
      {label}
    </a>
  )
}

/** The catalogue grew past this app: the parameter's type says no known component is left. */
function Unknown({ component }: { component: never }) {
  const type = (component as { component?: unknown }).component
  return <ComponentError>This app cannot show a "{String(type)}" component.</ComponentError>
}

function ComponentError({ children }: { children: ReactNode }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>This part of the view could not be shown</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

/** One component, or an error in its place when it throws while it renders. */
class ComponentBoundary extends Component<{ children: ReactNode }, { failure?: string }> {
  state: { failure?: string } = {}

  static getDerivedStateFromError(error: unknown) {
    return { failure: reason(error) }
  }

  render() {
    if (this.state.failure === undefined) return this.props.children
    return <ComponentError>{this.state.failure}</ComponentError>
  }
}

/** A text prop's value. A binding to nothing, or to something that is not a value, is empty. */
function text(prop: TextProp | null | undefined, scope: Scope): string {
  if (prop == null || typeof prop === "string") return prop ?? ""
  const value = resolve(scope.data, prop.path)
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : ""
}

/** The number a binding names, or none. */
function number(binding: Binding, scope: Scope): number | null {
  const value = resolve(scope.data, binding.path)
  return typeof value === "number" ? value : null
}

function flag(prop: boolean | Binding, scope: Scope): boolean {
  return typeof prop === "boolean" ? prop : resolve(scope.data, prop.path) === true
}

/** The array a binding names, or none when it names something else. */
function items(binding: Binding, scope: Scope): unknown[] {
  const value = resolve(scope.data, binding.path)
  return Array.isArray(value) ? (value as unknown[]) : []
}

/** What an RFC 6901 JSON Pointer names in `data`, if it names anything. */
function resolve(data: unknown, pointer: string): unknown {
  let value = data
  for (const token of pointer.split("/").slice(1)) {
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~")
    if (Array.isArray(value)) value = (value as unknown[])[Number(key)]
    else if (typeof value === "object" && value !== null && Object.hasOwn(value, key)) {
      value = (value as Record<string, unknown>)[key]
    } else return undefined
  }
  return value
}

function opens(href: string): boolean {
  try {
    return LINK_PROTOCOLS.has(new URL(href).protocol)
  } catch {
    return false
  }
}
