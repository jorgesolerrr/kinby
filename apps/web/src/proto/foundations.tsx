// PROTOTYPE for #978 (wayfinder map #975). Throwaway: lives only on the prototype/foundations branch.
// Two foundation variants side by side, each in light and dark, at /proto/foundations.
// A: Warm, Claude-like, on shadcn's luma style. B: Neutral, ChatGPT-like, on shadcn's rhea style.
import { cn } from "cn"
import {
  ArrowUpIcon,
  ChevronRightIcon,
  CircleIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import type { ReactNode } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import * as lumaAvatar from "./luma/avatar"
import * as lumaBadge from "./luma/badge"
import * as lumaButton from "./luma/button"
import * as lumaCard from "./luma/card"
import * as lumaCheckbox from "./luma/checkbox"
import * as lumaField from "./luma/field"
import * as lumaInput from "./luma/input"
import * as lumaSeparator from "./luma/separator"
import * as lumaSwitch from "./luma/switch"
import * as lumaTextarea from "./luma/textarea"
import * as rheaAvatar from "./rhea/avatar"
import * as rheaBadge from "./rhea/badge"
import * as rheaButton from "./rhea/button"
import * as rheaCard from "./rhea/card"
import * as rheaCheckbox from "./rhea/checkbox"
import * as rheaField from "./rhea/field"
import * as rheaInput from "./rhea/input"
import * as rheaSeparator from "./rhea/separator"
import * as rheaSwitch from "./rhea/switch"
import * as rheaTextarea from "./rhea/textarea"

import "./foundations.css"

const luma = {
  ...lumaAvatar,
  ...lumaBadge,
  ...lumaButton,
  ...lumaCard,
  ...lumaCheckbox,
  ...lumaField,
  ...lumaInput,
  ...lumaSeparator,
  ...lumaSwitch,
  ...lumaTextarea,
}
type Kit = typeof luma
const rhea: Kit = {
  ...rheaAvatar,
  ...rheaBadge,
  ...rheaButton,
  ...rheaCard,
  ...rheaCheckbox,
  ...rheaField,
  ...rheaInput,
  ...rheaSeparator,
  ...rheaSwitch,
  ...rheaTextarea,
}

interface TypeRow {
  role: string
  meta: string
  className: string
  sample: string
}

interface Variant {
  key: "a" | "b"
  name: string
  pitch: string
  facts: string[]
  kit: Kit
  type: TypeRow[]
  panel: string
  section: string
  kinRule: string
}

const REPLY =
  "I checked the three flights you saved. The 9:40 to Lisbon is still the cheapest, and it lands before your 2 pm call. Want me to hold a seat?"

const VARIANTS: Variant[] = [
  {
    key: "a",
    name: "A · Warm",
    pitch:
      "Claude-like. Warm paper neutrals, roomy, a serif for replies, kin on the primary button.",
    facts: ["luma style", "radius 10px", "button 36px", "UI 14 · reply 16 serif"],
    kit: luma,
    panel: "gap-10 p-5 sm:p-8",
    section: "gap-4",
    kinRule:
      "Primary button, selected row, focus ring, links, live dot, switch and checkbox, the mark.",
    type: [
      {
        role: "Greeting",
        meta: "Gabarito 30/36 semibold",
        className: "proto-heading text-3xl font-semibold tracking-tight",
        sample: "Good morning, Jorge",
      },
      {
        role: "Page title",
        meta: "Gabarito 20/28 semibold",
        className: "proto-heading text-xl font-semibold",
        sample: "Instances",
      },
      {
        role: "Section",
        meta: "Gabarito 16/24 semibold",
        className: "proto-heading text-base font-semibold",
        sample: "Routines",
      },
      {
        role: "UI",
        meta: "Geist 14/20",
        className: "text-sm",
        sample: "Ada is reading your inbox. Three routines run today.",
      },
      {
        role: "Reply",
        meta: "Source Serif 4, 16/28",
        className: "proto-reading text-base leading-7",
        sample: REPLY,
      },
      {
        role: "Meta",
        meta: "Geist 12/16, text 2",
        className: "text-xs text-muted-foreground",
        sample: "Updated 2 min ago · home instance",
      },
      {
        role: "Mono",
        meta: "Geist Mono 13/20",
        className: "font-mono text-[13px]",
        sample: "kinby hub --port 8080",
      },
    ],
  },
  {
    key: "b",
    name: "B · Neutral",
    pitch: "ChatGPT-like. Pure greys, compact, one sans everywhere, an ink primary button.",
    facts: ["rhea style", "radius 8px", "button 32px", "UI 14 · reply 15 sans"],
    kit: rhea,
    panel: "gap-7 p-5 sm:p-6",
    section: "gap-3",
    kinRule: "Focus ring, links, live dot, the mark. The primary button and selection stay ink.",
    type: [
      {
        role: "Greeting",
        meta: "Geist 24/32 semibold",
        className: "proto-heading text-2xl font-semibold tracking-tight",
        sample: "Good morning, Jorge",
      },
      {
        role: "Page title",
        meta: "Geist 18/28 semibold",
        className: "proto-heading text-lg font-semibold",
        sample: "Instances",
      },
      {
        role: "Section",
        meta: "Geist 14/20 semibold",
        className: "proto-heading text-sm font-semibold",
        sample: "Routines",
      },
      {
        role: "UI",
        meta: "Geist 14/20",
        className: "text-sm",
        sample: "Ada is reading your inbox. Three routines run today.",
      },
      {
        role: "Reply",
        meta: "Geist 15/28",
        className: "proto-reading text-[15px] leading-7",
        sample: REPLY,
      },
      {
        role: "Meta",
        meta: "Geist 12/16, text 2",
        className: "text-xs text-muted-foreground",
        sample: "Updated 2 min ago · home instance",
      },
      {
        role: "Mono",
        meta: "Geist Mono 13/20",
        className: "font-mono text-[13px]",
        sample: "kinby hub --port 8080",
      },
    ],
  },
]

const NEUTRALS: [token: string, label: string][] = [
  ["sidebar", "sidebar"],
  ["background", "thread"],
  ["card", "card"],
  ["secondary", "soft fill"],
  ["accent", "selected"],
  ["border", "border"],
  ["input", "field fill"],
  ["muted-foreground", "text 2"],
  ["foreground", "text 1"],
]
const SIGNALS: [token: string, label: string][] = [
  ["primary", "primary"],
  ["kin", "kin"],
  ["ember", "ember"],
  ["success", "success"],
  ["destructive", "danger"],
]

const BUTTON_VARIANTS = ["default", "secondary", "outline", "ghost", "destructive", "link"] as const
const BUTTON_SIZES = ["xs", "sm", "default", "lg"] as const
const ICON_SIZES = ["icon-xs", "icon-sm", "icon", "icon-lg"] as const

type Mode = "both" | "light" | "dark"

export function Foundations() {
  const [mode, setMode] = useState<Mode>("both")
  const darks = mode === "both" ? [false, true] : [mode === "dark"]

  return (
    <div className="min-h-svh bg-muted">
      <header className="flex flex-col gap-3 border-b bg-background px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex flex-col gap-0.5">
          <p className="text-xs text-muted-foreground">Prototype · #978 · map #975</p>
          <h1 className="text-lg font-semibold">Foundations: type, color, density, buttons</h1>
        </div>
        <ToggleGroup
          variant="outline"
          value={[mode]}
          onValueChange={(value: string[]) => {
            const next = value[0]
            if (next === "both" || next === "light" || next === "dark") setMode(next)
          }}
        >
          <ToggleGroupItem value="both">Both</ToggleGroupItem>
          <ToggleGroupItem value="light">Light</ToggleGroupItem>
          <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
        </ToggleGroup>
      </header>
      <main className="grid gap-px bg-border lg:grid-cols-2">
        {darks.flatMap((dark) =>
          VARIANTS.map((variant) => (
            <Board key={`${variant.key}-${dark}`} variant={variant} dark={dark} />
          )),
        )}
      </main>
    </div>
  )
}

function Board({ variant, dark }: { variant: Variant; dark: boolean }) {
  const { Badge } = variant.kit
  return (
    <section
      className={cn(
        `proto-${variant.key} proto-ui flex min-w-0 flex-col bg-background text-foreground`,
        dark && "dark",
        variant.panel,
      )}
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <KinbyMark className="size-7" />
          <span className="proto-heading text-lg font-semibold tracking-tight">kinby</span>
          <span className="ml-auto text-xs text-muted-foreground">{dark ? "dark" : "light"}</span>
        </div>
        <div className="flex flex-col gap-1">
          <h2 className="proto-heading text-2xl font-semibold">{variant.name}</h2>
          <p className="text-sm text-muted-foreground">{variant.pitch}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {variant.facts.map((fact) => (
            <Badge key={fact} variant="outline">
              {fact}
            </Badge>
          ))}
        </div>
      </div>
      <Part title="Type" variant={variant}>
        <TypeSpecimen rows={variant.type} />
      </Part>
      <Part title="Neutrals and signals" variant={variant}>
        <Swatches tokens={NEUTRALS} />
        <Swatches tokens={SIGNALS} />
      </Part>
      <Part title="Where kin appears" variant={variant}>
        <AccentUse variant={variant} />
      </Part>
      <Part title="Buttons" variant={variant}>
        <Buttons kit={variant.kit} />
      </Part>
      <Part title="Inputs" variant={variant}>
        <Inputs kit={variant.kit} />
      </Part>
      <Part title="Sample card" variant={variant}>
        <InstanceCard kit={variant.kit} />
      </Part>
    </section>
  )
}

function Part({
  title,
  variant,
  children,
}: {
  title: string
  variant: Variant
  children: ReactNode
}) {
  const { Separator } = variant.kit
  return (
    <div className={cn("flex flex-col", variant.section)}>
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {title}
        </h3>
        <Separator />
      </div>
      {children}
    </div>
  )
}

function TypeSpecimen({ rows }: { rows: TypeRow[] }) {
  return (
    <dl className="flex flex-col gap-4">
      {rows.map((row) => (
        <div key={row.role} className="grid gap-1 sm:grid-cols-[7.5rem_1fr] sm:gap-4">
          <dt className="flex flex-col text-xs">
            <span className="font-medium">{row.role}</span>
            <span className="text-muted-foreground">{row.meta}</span>
          </dt>
          <dd className={cn("min-w-0", row.className)}>{row.sample}</dd>
        </div>
      ))}
    </dl>
  )
}

function Swatches({ tokens }: { tokens: [string, string][] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(4.25rem,1fr))] gap-2">
      {tokens.map(([token, label]) => (
        <div key={token} className="flex flex-col gap-1">
          <div
            className="h-10 rounded-md ring-1 ring-foreground/10"
            style={{ background: `var(--${token})` }}
          />
          <span className="truncate text-[11px] leading-tight">{label}</span>
          <span className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
            --{token}
          </span>
        </div>
      ))}
    </div>
  )
}

const NAV = [
  { name: "Ada", note: "home instance", live: true },
  { name: "Scout", note: "research", live: true },
  { name: "Ledger", note: "stopped", live: false },
]

function AccentUse({ variant }: { variant: Variant }) {
  const { Button, Input, Switch, Checkbox, Badge } = variant.kit
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{variant.kinRule}</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <nav className="flex flex-col gap-0.5 rounded-xl bg-sidebar p-1.5">
          {NAV.map((item, index) => (
            <div
              key={item.name}
              className={cn(
                "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm",
                index === 0 ? "bg-accent font-medium text-accent-foreground" : "hover:bg-muted",
              )}
            >
              <CircleIcon
                className={cn(
                  "size-2",
                  item.live ? "fill-kin text-kin" : "fill-muted-foreground/40 text-transparent",
                )}
              />
              <span>{item.name}</span>
              <span className="ml-auto text-xs text-muted-foreground">{item.note}</span>
            </div>
          ))}
        </nav>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button>
              <PlusIcon data-icon="inline-start" />
              New thread
            </Button>
            <Badge variant="secondary">
              <CircleIcon className="fill-kin text-kin" data-icon="inline-start" />
              running
            </Badge>
          </div>
          <Input defaultValue="Focused input" className="border-ring ring-3 ring-ring/30" />
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              <Switch defaultChecked /> Routines on
            </label>
            <label className="flex items-center gap-2">
              <Checkbox defaultChecked /> Remember
            </label>
          </div>
          <p className="text-sm">
            Read the{" "}
            <a href="#978" className="font-medium text-kin underline-offset-4 hover:underline">
              routine log
            </a>{" "}
            for details.
          </p>
        </div>
      </div>
    </div>
  )
}

function Buttons({ kit }: { kit: Kit }) {
  const { Button } = kit
  return (
    <div className="flex flex-col gap-3">
      {BUTTON_VARIANTS.map((variant) => (
        <div key={variant} className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
          <span className="w-20 shrink-0 font-mono text-[11px] text-muted-foreground">
            {variant}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            {BUTTON_SIZES.map((size) => (
              <Button key={size} variant={variant} size={size}>
                {size === "default" ? "Default" : size}
              </Button>
            ))}
          </div>
        </div>
      ))}
      <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
        <span className="w-20 shrink-0 font-mono text-[11px] text-muted-foreground">icon</span>
        <div className="flex flex-wrap items-center gap-2">
          {ICON_SIZES.map((size) => (
            <Button key={size} variant="ghost" size={size} aria-label="Settings">
              <SettingsIcon />
            </Button>
          ))}
          {ICON_SIZES.map((size) => (
            <Button key={`o-${size}`} variant="outline" size={size} aria-label="Search">
              <SearchIcon />
            </Button>
          ))}
          <Button size="icon" aria-label="Send">
            <ArrowUpIcon />
          </Button>
          <Button disabled>Disabled</Button>
        </div>
      </div>
    </div>
  )
}

function Inputs({ kit }: { kit: Kit }) {
  const { Field, FieldGroup, FieldLabel, FieldDescription, FieldError, Input, Textarea, Switch } =
    kit
  return (
    <FieldGroup>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor="name">Instance name</FieldLabel>
          <Input id="name" placeholder="Ada" />
          <FieldDescription>Shown in the sidebar and in channels.</FieldDescription>
        </Field>
        <Field data-invalid>
          <FieldLabel htmlFor="port">Port</FieldLabel>
          <Input id="port" defaultValue="80" aria-invalid />
          <FieldError>Pick a port above 1024.</FieldError>
        </Field>
      </div>
      <Field data-disabled>
        <FieldLabel htmlFor="model">Model</FieldLabel>
        <Input id="model" defaultValue="claude-opus-5-5" disabled />
      </Field>
      <Field>
        <FieldLabel htmlFor="note">Standing instructions</FieldLabel>
        <Textarea id="note" placeholder="Reply in Spanish unless I write in English." />
      </Field>
      <Field orientation="horizontal">
        <Switch id="proactive" defaultChecked />
        <FieldLabel htmlFor="proactive">Let Ada start threads on her own</FieldLabel>
      </Field>
    </FieldGroup>
  )
}

const STATS: [label: string, value: string][] = [
  ["threads", "42"],
  ["routines", "3"],
  ["memories", "1,204"],
]

function InstanceCard({ kit }: { kit: Kit }) {
  const {
    Card,
    CardHeader,
    CardTitle,
    CardDescription,
    CardAction,
    CardContent,
    CardFooter,
    Avatar,
    AvatarFallback,
    Badge,
    Button,
  } = kit
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <Avatar>
            <AvatarFallback className="bg-avatar-green text-avatar-foreground">AD</AvatarFallback>
          </Avatar>
          <div className="flex flex-col gap-0.5">
            <CardTitle>Ada</CardTitle>
            <CardDescription>Home instance · claude-opus-5-5</CardDescription>
          </div>
        </div>
        <CardAction>
          <Badge variant="secondary">
            <CircleIcon className="fill-kin text-kin" data-icon="inline-start" />
            running
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Watches your inbox and calendar, keeps the trip folder tidy, and checks in each morning at
          8.
        </p>
        <dl className="grid grid-cols-3 gap-2">
          {STATS.map(([label, value]) => (
            <div key={label} className="flex flex-col rounded-lg bg-muted px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-base font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="gap-2">
        <Button>
          Open thread
          <ChevronRightIcon data-icon="inline-end" />
        </Button>
        <Button variant="outline">Config</Button>
        <Button variant="ghost" size="icon" className="ml-auto" aria-label="Remove">
          <Trash2Icon />
        </Button>
      </CardFooter>
    </Card>
  )
}
