import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  Recharts,
} from "@kinby/web"

/** Charts draw with the bundle's recharts (Kinby.Recharts), the copy ChartContainer sizes. */
const { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } = Recharts

const DAYS = [
  { day: "Oct 6", cost: 4.2, claude: 4, chatgpt: 1, input: 182, output: 41, done: 96, failed: 4 },
  { day: "Oct 7", cost: 6.8, claude: 6, chatgpt: 2, input: 240, output: 58, done: 92, failed: 8 },
  { day: "Oct 8", cost: 3.1, claude: 2, chatgpt: 0, input: 120, output: 30, done: 98, failed: 2 },
  { day: "Oct 9", cost: 8.4, claude: 7, chatgpt: 3, input: 310, output: 77, done: 89, failed: 11 },
  { day: "Oct 10", cost: 5.5, claude: 5, chatgpt: 1, input: 205, output: 49, done: 94, failed: 6 },
  { day: "Oct 11", cost: 1.9, claude: 1, chatgpt: 0, input: 74, output: 18, done: 100, failed: 0 },
  { day: "Oct 12", cost: 2.6, claude: 2, chatgpt: 1, input: 98, output: 25, done: 97, failed: 3 },
  { day: "Oct 13", cost: 9.7, claude: 4, chatgpt: 0, input: 352, output: 90, done: 91, failed: 9 },
]

function Axes({ unit }: { unit?: string }) {
  return (
    <>
      <CartesianGrid vertical={false} />
      <XAxis dataKey="day" tickLine={false} axisLine={false} minTickGap={16} />
      <YAxis
        tickLine={false}
        axisLine={false}
        width={40}
        tickFormatter={(value: number) => (unit === "$" ? `$${value}` : `${value}${unit ?? ""}`)}
      />
    </>
  )
}

const COST = { cost: { label: "API cost", color: "var(--chart-1)" } } satisfies ChartConfig

export const ApiCost = () => (
  <ChartContainer config={COST} className="aspect-auto h-48 w-full">
    <BarChart data={DAYS} accessibilityLayer>
      <Axes unit="$" />
      <Bar dataKey="cost" fill="var(--color-cost)" radius={4} isAnimationActive={false} />
    </BarChart>
  </ChartContainer>
)

const PLANS = {
  claude: { label: "Claude plan", color: "var(--chart-3)" },
  chatgpt: { label: "ChatGPT plan", color: "var(--chart-5)" },
} satisfies ChartConfig

export const PlanRuns = () => (
  <ChartContainer config={PLANS} className="aspect-auto h-48 w-full">
    <BarChart data={DAYS} accessibilityLayer>
      <Axes />
      <ChartLegend content={<ChartLegendContent />} />
      <Bar dataKey="claude" stackId="runs" fill="var(--color-claude)" isAnimationActive={false} />
      <Bar dataKey="chatgpt" stackId="runs" fill="var(--color-chatgpt)" isAnimationActive={false} />
    </BarChart>
  </ChartContainer>
)

const TOKENS = {
  input: { label: "Input", color: "var(--chart-1)" },
  output: { label: "Output", color: "var(--chart-2)" },
} satisfies ChartConfig

export const TokensWithTooltip = () => (
  <ChartContainer config={TOKENS} className="aspect-auto h-48 w-full">
    <AreaChart data={DAYS} accessibilityLayer>
      <Axes unit="K" />
      <ChartTooltip
        defaultIndex={3}
        active
        content={<ChartTooltipContent indicator="line" />}
      />
      <Area
        dataKey="input"
        type="monotone"
        stackId="tokens"
        fill="var(--color-input)"
        fillOpacity={0.3}
        stroke="var(--color-input)"
        isAnimationActive={false}
      />
      <Area
        dataKey="output"
        type="monotone"
        stackId="tokens"
        fill="var(--color-output)"
        fillOpacity={0.3}
        stroke="var(--color-output)"
        isAnimationActive={false}
      />
    </AreaChart>
  </ChartContainer>
)

const QUALITY = {
  done: { label: "Turns done", color: "var(--chart-2)" },
  failed: { label: "Turns failed", color: "var(--chart-4)" },
} satisfies ChartConfig

export const Quality = () => (
  <ChartContainer config={QUALITY} className="aspect-auto h-48 w-full">
    <LineChart data={DAYS} accessibilityLayer>
      <Axes unit="%" />
      <ChartLegend content={<ChartLegendContent />} />
      <Line
        dataKey="done"
        type="monotone"
        stroke="var(--color-done)"
        strokeWidth={2}
        dot={false}
        isAnimationActive={false}
      />
      <Line
        dataKey="failed"
        type="monotone"
        stroke="var(--color-failed)"
        strokeWidth={2}
        dot={false}
        isAnimationActive={false}
      />
    </LineChart>
  </ChartContainer>
)
