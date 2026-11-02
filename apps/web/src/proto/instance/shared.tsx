// PROTOTYPE: leaf pieces both instance-page variants draw: the config section bodies, the charts,
// the lifecycle alerts and dialogs. Layout stays in each variant. Throwaway.
import { CircleAlertIcon, DownloadIcon, PlayIcon, SquareIcon } from "lucide-react"
import { useState } from "react"
import { Bar, BarChart, XAxis } from "recharts"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import {
  BEHAVIOR_PROMPT,
  CORE,
  DAILY,
  type Life,
  MANIFEST,
  PERMISSIONS,
  SECRETS,
  SKILLS,
  TOOLSET,
} from "./mock"

export interface PageProps {
  instanceId: string
  tab: import("../shell/mock").InstanceTab
  go: import("../shell/screens").Go
  /** Kin's lifecycle state from the bar; other instances follow their mock state. */
  life: Life | undefined
  onLife: (life: Life) => void
}

/** One line on what the instance is doing, for under its name. */
export function statusLine(life: Life) {
  switch (life) {
    case "running":
      return `Running · core ${CORE.running} · up ${"3 days"}`
    case "update":
      return `Running · core ${CORE.running}, ${CORE.hub} available`
    case "stopped":
      return "Stopped · it does nothing until it starts"
    case "failed":
      return "Restarting · its container keeps stopping"
  }
}

export function LifeBadge({ life }: { life: Life }) {
  if (life === "stopped") return <Badge variant="outline">Stopped</Badge>
  if (life === "failed") return <Badge variant="destructive">Restarting</Badge>
  return <Badge variant="success">Running</Badge>
}

/** The alert a state other than plain running puts above the page. */
export function LifeAlert({ life, onLife }: { life: Life; onLife: (l: Life) => void }) {
  if (life === "failed") {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon />
        <AlertTitle>Kin is restarting</AlertTitle>
        <AlertDescription>
          Its container keeps stopping: MCP server notion failed to start. Check its settings in
          Config before you stop it.
        </AlertDescription>
        <AlertAction>
          <Button size="sm" variant="outline">
            Open Config
          </Button>
        </AlertAction>
      </Alert>
    )
  }
  if (life === "update") {
    return (
      <Alert>
        <DownloadIcon />
        <AlertTitle>Core {CORE.hub} is available</AlertTitle>
        <AlertDescription>
          Kin runs {CORE.running}. Updating recreates its container; its data stays.
        </AlertDescription>
        <AlertAction>
          <UpdateButton onDone={() => onLife("running")} />
        </AlertAction>
      </Alert>
    )
  }
  return null
}

export function UpdateButton({
  onDone,
  variant = "default",
}: {
  onDone: () => void
  variant?: "default" | "outline"
}) {
  return (
    <Button size="sm" variant={variant} onClick={onDone}>
      Update to {CORE.hub}
    </Button>
  )
}

/** Start, or Stop behind its confirmation. */
export function StartStop({
  life,
  onLife,
  size = "sm",
}: {
  life: Life
  onLife: (l: Life) => void
  size?: "sm" | "default"
}) {
  const [confirm, setConfirm] = useState(false)
  if (life === "stopped") {
    return (
      <Button size={size} onClick={() => onLife("running")}>
        <PlayIcon data-icon="inline-start" />
        Start
      </Button>
    )
  }
  return (
    <>
      <Button size={size} variant="outline" onClick={() => setConfirm(true)}>
        <SquareIcon data-icon="inline-start" />
        Stop
      </Button>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop Kin?</AlertDialogTitle>
            <AlertDialogDescription>
              Kin finishes the work it has accepted, then does nothing until it starts again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => onLife("stopped")}>Stop</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

export function RemoveDialog({
  open,
  onOpenChange,
  name,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  name: string
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
          <AlertDialogDescription>
            {name} stops and leaves the sidebar. Its data stays, and you can restore it from
            Settings → Instances.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive">Remove</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const TURNS: ChartConfig = {
  completed: { label: "Completed", color: "var(--chart-1)" },
  failed: { label: "Failed", color: "var(--destructive)" },
  interrupted: { label: "Interrupted", color: "var(--chart-3)" },
}

export function TurnsChart({ className }: { className?: string }) {
  return (
    <ChartContainer config={TURNS} className={className ?? "aspect-auto h-44 w-full"}>
      <BarChart data={DAILY} accessibilityLayer>
        <XAxis dataKey="day" tickLine={false} axisLine={false} interval={4} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="completed" stackId="a" fill="var(--color-completed)" />
        <Bar dataKey="interrupted" stackId="a" fill="var(--color-interrupted)" />
        <Bar dataKey="failed" stackId="a" fill="var(--color-failed)" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}

const COST: ChartConfig = { cost: { label: "API cost ($)", color: "var(--chart-2)" } }

export function CostChart({ className }: { className?: string }) {
  return (
    <ChartContainer config={COST} className={className ?? "aspect-auto h-36 w-full"}>
      <BarChart data={DAILY} accessibilityLayer>
        <XAxis dataKey="day" tickLine={false} axisLine={false} interval={4} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="cost" fill="var(--color-cost)" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}

export function RangeToggle() {
  return (
    <ToggleGroup defaultValue={["30d"]} variant="outline" size="sm">
      <ToggleGroupItem value="7d">7 days</ToggleGroupItem>
      <ToggleGroupItem value="30d">30 days</ToggleGroupItem>
      <ToggleGroupItem value="90d">90 days</ToggleGroupItem>
    </ToggleGroup>
  )
}

/* ---------- config section bodies ---------- */

function ModelSelect({ label, value, none }: { label: string; value: string; none?: string }) {
  const models = [
    "anthropic:claude-opus-5-5",
    "anthropic:claude-sonnet-5-5",
    "anthropic:claude-haiku-5-5",
    "openai:gpt-6",
  ]
  const items = [
    ...(none ? [{ value: "", label: none }] : []),
    ...models.map((m) => ({ value: m, label: m })),
  ]
  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      <Select items={items} defaultValue={value}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
                {i.value === "openai:gpt-6" && <Badge variant="outline">No key</Badge>}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>
  )
}

/** A label and its control on one row: how dense config stays readable. */
function Row({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <Field orientation="horizontal">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <FieldLabel>{label}</FieldLabel>
        {description && <FieldDescription>{description}</FieldDescription>}
      </div>
      {children}
    </Field>
  )
}

export function ManifestBody() {
  const m = MANIFEST
  return (
    <FieldGroup>
      <FieldSet>
        <FieldLabel>Models</FieldLabel>
        <div className="grid gap-4 md:grid-cols-3">
          <ModelSelect label="Main" value={m.models.main} />
          <ModelSelect label="Recap" value={m.models.recap} none="Same as the main model" />
          <ModelSelect label="Embedding" value={m.models.embed} none="No embeddings" />
        </div>
      </FieldSet>
      <FieldSet>
        <FieldLabel>Budgets per day</FieldLabel>
        <FieldDescription>Blank means no limit.</FieldDescription>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {(
            [
              ["Steps", m.budgets.steps],
              ["Tokens", m.budgets.tokens],
              ["Seconds", m.budgets.seconds],
              ["Dollars", m.budgets.dollars],
            ] as const
          ).map(([label, value]) => (
            <Field key={label}>
              <FieldLabel>{label}</FieldLabel>
              <Input defaultValue={value} inputMode="numeric" placeholder="No limit" />
            </Field>
          ))}
        </div>
      </FieldSet>
      <FieldSet>
        <FieldLabel>Running</FieldLabel>
        <Row label="Routines timezone">
          <Input className="w-48" defaultValue={m.timezone} />
        </Row>
        <Row label="Shell timeout" description="Seconds a shell command may run">
          <Input className="w-24" defaultValue={m.shellTimeout} inputMode="numeric" />
        </Row>
        <Row label="Default tools" description="Browser, shell, files, web search">
          <Switch defaultChecked={m.defaultTools} />
        </Row>
        <Row label="Recap" description="Write a recap into memory after each turn">
          <ToggleGroup defaultValue={[m.recap]} variant="outline" size="sm">
            <ToggleGroupItem value="every">Every turn</ToggleGroupItem>
            <ToggleGroupItem value="off">Off</ToggleGroupItem>
          </ToggleGroup>
        </Row>
        <Row label="Ask for a rating" description="Show 👍 👎 at the end of a turn">
          <Switch defaultChecked={m.askRating} />
        </Row>
        <Row label="Persona name">
          <Input className="w-48" defaultValue={m.persona} />
        </Row>
      </FieldSet>
      <div className="flex justify-end gap-2">
        <Button variant="ghost">Discard</Button>
        <Button>Save</Button>
      </div>
    </FieldGroup>
  )
}

export function PromptBody({ file }: { file: string }) {
  return (
    <FieldGroup>
      <Field>
        <div className="flex items-center gap-2">
          <FieldLabel className="font-mono">{file}</FieldLabel>
          <span className="ml-auto text-xs text-muted-foreground">Last changed by you, 28 Oct</span>
        </div>
        <Textarea className="min-h-80 font-mono text-sm" defaultValue={BEHAVIOR_PROMPT} />
        <FieldDescription>Saved changes apply at the next turn.</FieldDescription>
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost">Restore default</Button>
        <Button>Save</Button>
      </div>
    </FieldGroup>
  )
}

export function PermissionsBody() {
  const p = PERMISSIONS
  return (
    <FieldGroup>
      <Row label="Ceiling" description="The most a routine or a thread may ask for">
        <Badge variant="secondary">{p.ceiling}</Badge>
      </Row>
      <Row label="Default mode">
        <ToggleGroup defaultValue={[p.mode]} variant="outline" size="sm">
          <ToggleGroupItem value="read-only">Read-only</ToggleGroupItem>
          <ToggleGroupItem value="ask">Ask</ToggleGroupItem>
          <ToggleGroupItem value="auto">Auto</ToggleGroupItem>
        </ToggleGroup>
      </Row>
      <FieldSet>
        <FieldLabel>Tool rules</FieldLabel>
        <ItemGroup>
          {p.rules.map((r) => (
            <Item key={r.tool} size="xs" variant="outline">
              <ItemContent>
                <ItemTitle className="font-mono">{r.tool}</ItemTitle>
              </ItemContent>
              <ItemActions>
                <ToggleGroup defaultValue={[r.rule]} variant="outline" size="sm">
                  <ToggleGroupItem value="allow">Allow</ToggleGroupItem>
                  <ToggleGroupItem value="ask">Ask</ToggleGroupItem>
                  <ToggleGroupItem value="deny">Deny</ToggleGroupItem>
                  <ToggleGroupItem value="follow">Follow mode</ToggleGroupItem>
                </ToggleGroup>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </FieldSet>
      <Row label="Always denied" description="Shell patterns">
        <span className="font-mono text-sm">{p.denied.join(", ")}</span>
      </Row>
      <Row label="Always ask" description="Shell patterns">
        <span className="font-mono text-sm">{p.ask.join(", ")}</span>
      </Row>
    </FieldGroup>
  )
}

export function SecretsBody() {
  return (
    <ItemGroup>
      {SECRETS.map((s) => (
        <Item key={s.variable} size="sm" variant="outline">
          <ItemContent>
            <ItemTitle>
              {s.label}
              {s.optional && <Badge variant="outline">Optional</Badge>}
            </ItemTitle>
            <ItemDescription className="font-mono">
              {s.variable}
              {s.hub && <span className="font-sans"> · from the hub secret {s.hub}</span>}
            </ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant={s.is_set ? "secondary" : "outline"}>
              {s.is_set ? "Set" : "Not set"}
            </Badge>
            <Button size="sm" variant="outline">
              {s.is_set ? "Replace" : "Set"}
            </Button>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

export function SkillsBody() {
  return (
    <ItemGroup>
      {SKILLS.map((s) => (
        <Item key={s.name} size="sm" variant="outline">
          <ItemContent>
            <ItemTitle className="font-mono">{s.name}</ItemTitle>
            <ItemDescription>{s.source}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Switch defaultChecked />
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

export function ToolsBody() {
  return (
    <ItemGroup>
      {TOOLSET.builtin.map((t) => (
        <Item key={t} size="xs" variant="outline">
          <ItemContent>
            <ItemTitle>{t}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <Switch defaultChecked />
          </ItemActions>
        </Item>
      ))}
      {TOOLSET.mcp.map((t) => (
        <Item key={t.name} size="xs" variant="outline">
          <ItemContent>
            <ItemTitle className="font-mono">{t.name}</ItemTitle>
            <ItemDescription>
              MCP server · {t.tools} tools{t.state === "failed" && " · failed to start"}
            </ItemDescription>
          </ItemContent>
          <ItemActions>
            {t.state === "failed" && <Badge variant="destructive">Failed</Badge>}
            <Switch defaultChecked />
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

export function CoreBody({ life, onLife }: { life: Life; onLife: (l: Life) => void }) {
  return (
    <ItemGroup>
      <Item variant="outline">
        <ItemContent>
          <ItemTitle>Core {CORE.running}</ItemTitle>
          <ItemDescription>
            {life === "update"
              ? `The hub's revision is ${CORE.hub}. Updating recreates the container; its data stays.`
              : "The hub's revision. Up to date."}
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          {life === "update" ? (
            <UpdateButton onDone={() => onLife("running")} />
          ) : (
            <Button size="sm" variant="outline">
              Another ref…
            </Button>
          )}
        </ItemActions>
      </Item>
    </ItemGroup>
  )
}

export function ConfigBody({
  id,
  life,
  onLife,
}: {
  id: string
  life: Life
  onLife: (l: Life) => void
}) {
  switch (id) {
    case "behavior":
      return <PromptBody file="SYSTEM.md" />
    case "recap":
      return <PromptBody file="RECAP.md" />
    case "permissions":
      return <PermissionsBody />
    case "manifest":
      return <ManifestBody />
    case "skills":
      return <SkillsBody />
    case "tools":
      return <ToolsBody />
    case "secrets":
      return <SecretsBody />
    default:
      return <CoreBody life={life} onLife={onLife} />
  }
}
