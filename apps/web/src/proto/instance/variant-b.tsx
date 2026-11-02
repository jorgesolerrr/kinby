// PROTOTYPE · Variant B, "Overview + section nav". No tab strip: the page is split like the
// Settings dialog, a nav down its left that lists Overview, Routines, Memory and Stats, then
// Config's eight sections flat under a Config label, so Config is no longer a list inside a tab.
// Overview is a dashboard: a status card that holds the lifecycle actions, what needs you, and a
// glance card per surface that opens it. Routines is a table that opens a sheet; Memory is a list
// and a reading pane; Stats is one scroll of cards. On a phone the nav becomes a picker.
import {
  ArrowRightIcon,
  CircleAlertIcon,
  GlobeIcon,
  KeyRoundIcon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
  UserRoundIcon,
} from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty"
import { Field, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "cn"

import { type InstanceTab, instanceById } from "../shell/mock"
import {
  CONFIG_SECTIONS,
  CORE,
  type Life,
  LOGINS,
  MEMORY,
  MEMORY_BODY,
  PROFILES,
  PROFILE_MD,
  ROUTINES,
  ROUTINE_MD,
  type Routine,
  STATS,
  TOOLSET,
  UPTIME,
  VAULT,
} from "./mock"
import {
  ConfigBody,
  CostChart,
  LifeBadge,
  type PageProps,
  RangeToggle,
  RemoveDialog,
  StartStop,
  TurnsChart,
  UpdateButton,
} from "./shared"

/** A nav entry: one of the shell's tabs, or one config section (`config:<id>`). */
type Spot = Exclude<InstanceTab, "config"> | `config:${string}`

const SURFACES: { id: Spot; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "routines", label: "Routines" },
  { id: "memory", label: "Memory" },
  { id: "stats", label: "Stats" },
]
const CONFIG: { id: Spot; label: string }[] = CONFIG_SECTIONS.map((s) => ({
  id: `config:${s.id}`,
  label: s.label,
}))

const NEEDS_RUNNING: Spot[] = ["routines", "memory", "stats"]

export function InstanceB({ instanceId, tab, go, life: kinLife, onLife }: PageProps) {
  const instance = instanceById(instanceId)
  const [ownLife, setOwnLife] = useState<Life>(instance.state === "stopped" ? "stopped" : "running")
  const life = kinLife ?? ownLife
  const setLife = kinLife === undefined ? setOwnLife : onLife
  const [configSection, setConfigSection] = useState(life === "update" ? "core" : "manifest")
  const [remove, setRemove] = useState(false)
  const spot: Spot = tab === "config" ? `config:${configSection}` : tab

  const open = (to: Spot) => {
    if (to.startsWith("config:")) {
      setConfigSection(to.slice("config:".length))
      go({ kind: "instance", instanceId, tab: "config" })
    } else {
      go({ kind: "instance", instanceId, tab: to as InstanceTab })
    }
  }
  const flag = (id: Spot) =>
    (id === "config:core" && life === "update") || (id === "config:tools" && life === "failed")
  const running = life !== "stopped"
  const items = [...SURFACES, ...CONFIG].map((s) => ({ value: s.id, label: s.label }))

  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <aside className="hidden w-56 shrink-0 flex-col gap-4 overflow-y-auto border-r p-4 md:flex">
        <div className="flex items-center gap-2 px-1">
          <InstanceAvatar avatar={instance.avatar} name={instance.name} />
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-heading font-semibold">{instance.name}</span>
            <LifeBadge life={life} />
          </div>
        </div>
        <NavList entries={SURFACES} spot={spot} open={open} flag={flag} />
        <div className="flex flex-col gap-1">
          <span className="px-3 text-xs font-medium text-muted-foreground">Config</span>
          <NavList entries={CONFIG} spot={spot} open={open} flag={flag} />
        </div>
      </aside>

      <div className="flex items-center gap-2 border-b p-3 md:hidden">
        <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
        <Select items={items} value={spot} onValueChange={(v) => v && open(v as Spot)}>
          <SelectTrigger className="flex-1">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectLabel>{instance.name}</SelectLabel>
              {SURFACES.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectGroup>
            <SelectGroup>
              <SelectLabel>Config</SelectLabel>
              {CONFIG.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <LifeBadge life={life} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-6 md:px-8">
          {!running && NEEDS_RUNNING.includes(spot) ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyTitle>{instance.name} is stopped</EmptyTitle>
                <EmptyDescription>Start it to see its {spot}.</EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button size="sm" onClick={() => setLife("running")}>
                  <PlayIcon data-icon="inline-start" />
                  Start {instance.name}
                </Button>
              </EmptyContent>
            </Empty>
          ) : spot === "overview" ? (
            <Overview
              name={instance.name}
              home={instance.home === true}
              life={life}
              onLife={setLife}
              open={open}
              onRemove={() => setRemove(true)}
            />
          ) : spot === "routines" ? (
            <Routines />
          ) : spot === "memory" ? (
            <Memory />
          ) : spot === "stats" ? (
            <Stats />
          ) : (
            <ConfigPage id={configSection} life={life} onLife={setLife} />
          )}
        </div>
      </div>
      <RemoveDialog open={remove} onOpenChange={setRemove} name={instance.name} />
    </div>
  )
}

function NavList({
  entries,
  spot,
  open,
  flag,
}: {
  entries: { id: Spot; label: string }[]
  spot: Spot
  open: (s: Spot) => void
  flag: (s: Spot) => boolean
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {entries.map((e) => (
        <Button
          key={e.id}
          size="sm"
          variant={e.id === spot ? "secondary" : "ghost"}
          className="justify-start"
          onClick={() => open(e.id)}
        >
          {e.label}
          {flag(e.id) && (
            <span aria-label="Needs a look" className="ml-auto size-2 rounded-full bg-ember" />
          )}
        </Button>
      ))}
    </div>
  )
}

function Heading({
  title,
  lead,
  action,
}: {
  title: string
  lead?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-end gap-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <h1 className="font-heading text-xl font-semibold">{title}</h1>
        {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
      </div>
      {action}
    </div>
  )
}

/* ---------- Overview: a dashboard ---------- */

function Overview({
  name,
  home,
  life,
  onLife,
  open,
  onRemove,
}: {
  name: string
  home: boolean
  life: Life
  onLife: (l: Life) => void
  open: (s: Spot) => void
  onRemove: () => void
}) {
  const pending = LOGINS.filter((l) => l.state !== "signed_in")
  const failing = ROUTINES.filter((r) => r.failures !== undefined)
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 font-heading text-lg">
            {name} <LifeBadge life={life} />
          </CardTitle>
          <CardDescription>
            {life === "stopped"
              ? "Stopped. It does nothing until it starts."
              : life === "failed"
                ? "Its container keeps stopping: MCP server notion failed to start."
                : `Up ${UPTIME} · core ${CORE.running}${life === "update" ? `, ${CORE.hub} available` : ""}`}
          </CardDescription>
          <CardAction>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button size="icon-sm" variant="ghost" aria-label="More actions" />}
              >
                <MoreHorizontalIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuItem onClick={() => open("config:core")}>
                    Core version…
                  </DropdownMenuItem>
                  <DropdownMenuItem variant="destructive" disabled={home} onClick={onRemove}>
                    <TrashIcon />
                    {home ? "The home instance stays" : `Remove ${name}…`}
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </CardAction>
        </CardHeader>
        <CardFooter className="gap-2">
          <StartStop life={life} onLife={onLife} />
          {life === "update" && <UpdateButton onDone={() => onLife("running")} variant="outline" />}
          {life === "failed" && (
            <Button size="sm" variant="outline" onClick={() => open("config:tools")}>
              Open Tools
            </Button>
          )}
        </CardFooter>
      </Card>

      {(pending.length > 0 || failing.length > 0) && life !== "stopped" && (
        <div className="flex flex-col gap-2">
          {pending.map((l) => (
            <Alert key={l.id}>
              <UserRoundIcon />
              <AlertTitle>{l.label} is not signed in</AlertTitle>
              <AlertDescription>{l.description}</AlertDescription>
              <AlertAction>
                <Button size="sm">Sign in</Button>
              </AlertAction>
            </Alert>
          ))}
          {failing.map((r) => (
            <Alert key={r.id}>
              <CircleAlertIcon />
              <AlertTitle>
                <span className="font-mono">{r.name}</span> failed {r.failures} times in a row
              </AlertTitle>
              <AlertDescription>Last at {r.last}</AlertDescription>
              <AlertAction>
                <Button size="sm" variant="outline" onClick={() => open("routines")}>
                  Open
                </Button>
              </AlertAction>
            </Alert>
          ))}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Glance
          title="Routines"
          onOpen={() => open("routines")}
          figure={`${ROUTINES.filter((r) => r.enabled).length} on`}
          lines={["Next: flight-watch, today 18:00", "1 waiting on an approval"]}
        />
        <Glance
          title="Memory"
          onOpen={() => open("memory")}
          figure="214"
          lines={["34 remembered in the last 30 days", "Latest: the dentist quotes, 31 Oct"]}
        />
        <Glance
          title="Last 30 days"
          onOpen={() => open("stats")}
          figure={STATS.tiles[1].value}
          lines={["182 turns · 6 failed", STATS.plans]}
        >
          <TurnsChart className="aspect-auto h-16 w-full" />
        </Glance>
        <Glance
          title="Config"
          onOpen={() => open("config:manifest")}
          figure="claude-opus-5-5"
          lines={["Budget $5 a day · ask before acting", "4 skills · 3 MCP servers"]}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <ListCard
          title="Browser profiles"
          action="New profile"
          rows={PROFILES.map((p) => ({ name: p.name, sub: p.site }))}
          icon={<GlobeIcon />}
        />
        <ListCard
          title="Vault"
          action="Add sign-in"
          rows={VAULT.map((v) => ({ name: v.name, sub: v.site }))}
          icon={<KeyRoundIcon />}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        Toolset: {TOOLSET.builtin.join(", ")}, and {TOOLSET.mcp.map((m) => m.name).join(", ")}.
      </p>
    </>
  )
}

function Glance({
  title,
  figure,
  lines,
  onOpen,
  children,
}: {
  title: string
  figure: string
  lines: string[]
  onOpen: () => void
  children?: React.ReactNode
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="truncate font-heading text-xl tabular-nums">{figure}</CardTitle>
        <CardAction>
          <Button size="icon-sm" variant="ghost" aria-label={`Open ${title}`} onClick={onOpen}>
            <ArrowRightIcon />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
        {children}
        {lines.map((l) => (
          <span key={l} className="truncate">
            {l}
          </span>
        ))}
      </CardContent>
    </Card>
  )
}

function ListCard({
  title,
  action,
  rows,
  icon,
}: {
  title: string
  action: string
  rows: { name: string; sub: string }[]
  icon: React.ReactNode
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardAction>
          <Button size="sm" variant="ghost">
            <PlusIcon data-icon="inline-start" />
            {action}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <ItemGroup className="gap-1">
          {rows.map((r) => (
            <Item key={r.name} size="xs">
              <ItemMedia variant="icon">{icon}</ItemMedia>
              <ItemContent>
                <ItemTitle>{r.name}</ItemTitle>
                <ItemDescription className="truncate">{r.sub}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button size="icon-xs" variant="ghost" aria-label="More">
                  <MoreHorizontalIcon />
                </Button>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </CardContent>
    </Card>
  )
}

/* ---------- Config: one section per nav entry ---------- */

function ConfigPage({ id, life, onLife }: { id: string; life: Life; onLife: (l: Life) => void }) {
  const section = CONFIG_SECTIONS.find((s) => s.id === id) ?? CONFIG_SECTIONS[0]
  return (
    <>
      <Heading title={section.label} lead={section.hint} />
      <ConfigBody id={section.id} life={life} onLife={onLife} />
    </>
  )
}

/* ---------- Routines: a table, the routine in a sheet ---------- */

const OUTCOME: Record<
  NonNullable<Routine["outcome"]>,
  { label: string; variant: "secondary" | "outline" | "destructive" | "attention" }
> = {
  work: { label: "Did work", variant: "secondary" },
  "no-work": { label: "Nothing to do", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
  parked: { label: "Needs you", variant: "attention" },
}

function Routines() {
  const [open, setOpen] = useState<Routine | undefined>()
  return (
    <>
      <Heading
        title="Routines"
        lead="What Kin does on a schedule or a signal"
        action={
          <Button size="sm">
            <PlusIcon data-icon="inline-start" />
            New routine
          </Button>
        }
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Routine</TableHead>
            <TableHead className="hidden md:table-cell">When</TableHead>
            <TableHead className="hidden sm:table-cell">Next</TableHead>
            <TableHead>Last</TableHead>
            <TableHead className="text-right">On</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ROUTINES.map((r) => (
            <TableRow
              key={r.id}
              className={cn("cursor-pointer", !r.enabled && "opacity-60")}
              onClick={() => setOpen(r)}
            >
              <TableCell>
                <div className="flex flex-col">
                  <span className="font-mono">{r.name}</span>
                  <span className="max-w-64 truncate text-xs text-muted-foreground">
                    {r.description}
                  </span>
                </div>
              </TableCell>
              <TableCell className="hidden md:table-cell">{r.human}</TableCell>
              <TableCell className="hidden sm:table-cell">{r.next ?? "—"}</TableCell>
              <TableCell>
                {r.outcome ? (
                  <Badge variant={OUTCOME[r.outcome].variant}>
                    {r.failures ? `Failed ×${r.failures}` : OUTCOME[r.outcome].label}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground">Never</span>
                )}
              </TableCell>
              <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                <Switch defaultChecked={r.enabled} aria-label={`${r.name} enabled`} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Sheet open={open !== undefined} onOpenChange={(o) => !o && setOpen(undefined)}>
        <SheetContent className="sm:max-w-lg">
          <SheetHeader>
            <SheetTitle className="font-mono">{open?.name}</SheetTitle>
            <SheetDescription>
              {open?.human} · {open?.trigger}
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-4 overflow-y-auto px-4">
            <p className="text-sm">{open?.description}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Next firing</dt>
              <dd>{open?.next ?? "No next firing"}</dd>
              <dt className="text-muted-foreground">Last firing</dt>
              <dd>{open?.last ?? "Never fired"}</dd>
              <dt className="text-muted-foreground">Mode</dt>
              <dd>{open?.mode}</dd>
            </dl>
            <Field>
              <FieldLabel className="font-mono">ROUTINE.md</FieldLabel>
              <Textarea className="min-h-56 font-mono text-sm" defaultValue={ROUTINE_MD} />
            </Field>
          </div>
          <SheetFooter className="flex-row">
            <Button variant="outline">
              <PlayIcon data-icon="inline-start" />
              Run now
            </Button>
            <Button variant="ghost">Delete</Button>
            <Button className="ml-auto">Save</Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  )
}

/* ---------- Memory: a list and a reading pane ---------- */

function Memory() {
  const [open, setOpen] = useState<string>("profile")
  const node = MEMORY.find((n) => n.node === open)
  return (
    <>
      <Heading
        title="Memory"
        lead="214 facts and episodes, and the profile in every prompt"
        action={
          <Button size="sm" variant="outline">
            <PlusIcon data-icon="inline-start" />
            Add fact
          </Button>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <InputGroup className="min-w-48 flex-1">
          <InputGroupInput placeholder="Search memory…" />
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
        </InputGroup>
        <ToggleGroup defaultValue={["all"]} variant="outline" size="sm">
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          <ToggleGroupItem value="fact">Facts</ToggleGroupItem>
          <ToggleGroupItem value="episode">Episodes</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <ItemGroup className="gap-1">
          <Item
            size="sm"
            variant={open === "profile" ? "muted" : "default"}
            render={<button type="button" onClick={() => setOpen("profile")} />}
          >
            <ItemMedia variant="icon">
              <UserRoundIcon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>Profile</ItemTitle>
              <ItemDescription>In every prompt · about 60 tokens</ItemDescription>
            </ItemContent>
          </Item>
          {MEMORY.map((n) => (
            <Item
              key={n.node}
              size="sm"
              variant={open === n.node ? "muted" : "default"}
              render={<button type="button" onClick={() => setOpen(n.node)} />}
            >
              <ItemContent>
                <ItemTitle className="line-clamp-2 text-left">{n.description}</ItemTitle>
                <ItemDescription>
                  {n.kind} · {n.date}
                </ItemDescription>
              </ItemContent>
            </Item>
          ))}
        </ItemGroup>
        <Card size="sm" className="self-start">
          {node === undefined ? (
            <>
              <CardHeader>
                <CardTitle className="font-mono">memory/profile.md</CardTitle>
                <CardDescription>It goes into every prompt, about 60 tokens.</CardDescription>
              </CardHeader>
              <CardContent>
                <Textarea className="min-h-48 font-mono text-sm" defaultValue={PROFILE_MD} />
              </CardContent>
              <CardFooter className="justify-end">
                <Button size="sm">Save</Button>
              </CardFooter>
            </>
          ) : (
            <>
              <CardHeader>
                <CardDescription>
                  <Badge variant="outline">{node.kind}</Badge> {node.date}
                </CardDescription>
                <CardTitle>{node.description}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                <div className="flex flex-wrap gap-1">
                  {node.subjects.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
                <p>{MEMORY_BODY}</p>
                <p className="text-muted-foreground">
                  Recap of a turn in “Plan the Lisbon trip in March”
                </p>
              </CardContent>
              <CardFooter className="gap-2">
                {node.kind === "fact" && (
                  <Button size="sm" variant="outline">
                    Correct
                  </Button>
                )}
                <Button size="sm" variant="ghost">
                  Forget
                </Button>
              </CardFooter>
            </>
          )}
        </Card>
      </div>
    </>
  )
}

/* ---------- Stats: one scroll of cards ---------- */

function Stats() {
  return (
    <>
      <Heading title="Stats" lead="Daily buckets, UTC" action={<RangeToggle />} />
      <Badge variant="outline" className="self-start">
        {STATS.plans}
      </Badge>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {STATS.tiles.map((t) => (
          <Card key={t.label} size="sm">
            <CardHeader>
              <CardDescription>{t.label}</CardDescription>
              <CardTitle className="font-heading text-2xl tabular-nums">{t.value}</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">{t.detail}</CardContent>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card size="sm">
          <CardHeader>
            <CardTitle>Turns</CardTitle>
            <CardDescription>Completed, interrupted and failed per day</CardDescription>
          </CardHeader>
          <CardContent>
            <TurnsChart />
          </CardContent>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardTitle>API cost</CardTitle>
            <CardDescription>$14.20 over 30 days</CardDescription>
          </CardHeader>
          <CardContent>
            <CostChart className="aspect-auto h-44 w-full" />
          </CardContent>
        </Card>
      </div>
      <Card size="sm">
        <CardHeader>
          <CardTitle>Where turns came from</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Origin</TableHead>
                <TableHead className="text-right">Turns</TableHead>
                <TableHead className="hidden text-right sm:table-cell">No work</TableHead>
                <TableHead className="text-right">Failed</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {STATS.origins.map((o) => (
                <TableRow key={o.origin}>
                  <TableCell className="font-medium">{o.origin}</TableCell>
                  <TableCell className="text-right tabular-nums">{o.turns}</TableCell>
                  <TableCell className="hidden text-right tabular-nums sm:table-cell">
                    {o.noWork}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{o.failed}</TableCell>
                  <TableCell className="text-right tabular-nums">{o.cost}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <div className="grid gap-4 md:grid-cols-3">
        <FigureCard
          title="Most-called tools"
          rows={STATS.tools.map((t) => [t.tool, t.calls] as const)}
        />
        <FigureCard
          title="Memory"
          rows={[
            ["Searches", STATS.memory.searches],
            ["Opens", STATS.memory.opens],
            ["Remembered", STATS.memory.remembered],
            ["Forgotten", STATS.memory.forgotten],
          ]}
        />
        <FigureCard
          title="Approvals"
          rows={[
            ["Asked", STATS.approvals.asked],
            ["Denied by policy", STATS.approvals.deniedPolicy],
            ["Denied by you", STATS.approvals.deniedYou],
          ]}
        />
      </div>
    </>
  )
}

function FigureCard({
  title,
  rows,
}: {
  title: string
  rows: readonly (readonly [string, number])[]
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="flex flex-col gap-1.5 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-2">
              <dt className="truncate text-muted-foreground">{label}</dt>
              <dd className="tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}
