// PROTOTYPE · Variant A, "Tabs + rows". The Settings dialog's Rows grammar, carried onto a page: a
// header that stays put with the instance's identity, its state and its lifecycle actions (Start or
// Stop, then a ⋯ with Update and Remove); line tabs under it; each tab one flat list of rows under
// a section header that holds its one action, unfolding in place. Config keeps its section list
// inside the tab; Stats is one scroll with a range at the top.
import {
  BrainIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  GlobeIcon,
  KeyRoundIcon,
  LayoutPanelLeftIcon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  ServerIcon,
  TrashIcon,
  UserRoundIcon,
} from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
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
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "cn"

import { INSTANCE_TABS, type InstanceTab, instanceById } from "../shell/mock"
import {
  CONFIG_SECTIONS,
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
  VAULT,
} from "./mock"
import {
  ConfigBody,
  CostChart,
  LifeAlert,
  type PageProps,
  RangeToggle,
  RemoveDialog,
  StartStop,
  TurnsChart,
  statusLine,
} from "./shared"

const NEEDS_RUNNING: InstanceTab[] = ["routines", "memory", "stats"]

export function InstanceA({ instanceId, tab, go, life: kinLife, onLife }: PageProps) {
  const instance = instanceById(instanceId)
  const [ownLife, setOwnLife] = useState<Life>(instance.state === "stopped" ? "stopped" : "running")
  const life = kinLife ?? ownLife
  const setLife = kinLife === undefined ? setOwnLife : onLife
  const [remove, setRemove] = useState(false)
  const running = life !== "stopped"

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Tabs
        value={tab}
        onValueChange={(v: InstanceTab) => go({ kind: "instance", instanceId, tab: v })}
        className="gap-0"
      >
        <div className="sticky top-0 z-10 bg-background">
          <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 pt-6 md:px-8">
            <div className="flex items-center gap-3">
              <InstanceAvatar avatar={instance.avatar} name={instance.name} size="lg" />
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex items-center gap-2">
                  <h1 className="truncate font-heading text-xl font-semibold">{instance.name}</h1>
                  {instance.home && <Badge variant="outline">Home</Badge>}
                </div>
                <p className="truncate text-sm text-muted-foreground">{statusLine(life)}</p>
              </div>
              <StartStop life={life} onLife={setLife} />
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={<Button size="icon-sm" variant="ghost" aria-label="More actions" />}
                >
                  <MoreHorizontalIcon />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuGroup>
                    <DropdownMenuItem
                      onClick={() => go({ kind: "instance", instanceId, tab: "config" })}
                    >
                      <RefreshCwIcon />
                      Update core…
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuItem
                      variant="destructive"
                      disabled={instance.home}
                      onClick={() => setRemove(true)}
                    >
                      <TrashIcon />
                      {instance.home ? "The home instance stays" : `Remove ${instance.name}…`}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <LifeAlert life={life} onLife={setLife} />
            <TabsList variant="line" className="w-full justify-start overflow-x-auto">
              {INSTANCE_TABS.map((t) => (
                <TabsTrigger key={t.id} value={t.id} className="flex-none">
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <Separator />
        </div>
        <div className="mx-auto w-full max-w-4xl px-4 py-6 md:px-8">
          {INSTANCE_TABS.map((t) => (
            <TabsContent key={t.id} value={t.id}>
              {!running && NEEDS_RUNNING.includes(t.id) ? (
                <NotRunning name={instance.name} tab={t.label} onStart={() => setLife("running")} />
              ) : (
                <Surface tab={t.id} life={life} onLife={setLife} />
              )}
            </TabsContent>
          ))}
        </div>
      </Tabs>
      <RemoveDialog open={remove} onOpenChange={setRemove} name={instance.name} />
    </div>
  )
}

function NotRunning({ name, tab, onStart }: { name: string; tab: string; onStart: () => void }) {
  return (
    <Empty className="border">
      <EmptyHeader>
        <EmptyTitle>{name} is stopped</EmptyTitle>
        <EmptyDescription>
          Its {tab.toLowerCase()} show while it runs. Config stays open while it's stopped.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button size="sm" onClick={onStart}>
          <PlayIcon data-icon="inline-start" />
          Start {name}
        </Button>
      </EmptyContent>
    </Empty>
  )
}

function Surface({
  tab,
  life,
  onLife,
}: {
  tab: InstanceTab
  life: Life
  onLife: (l: Life) => void
}) {
  switch (tab) {
    case "overview":
      return <Overview />
    case "config":
      return <Config life={life} onLife={onLife} />
    case "routines":
      return <Routines />
    case "memory":
      return <Memory />
    case "stats":
      return <Stats />
  }
}

/** A section's header: its title, a one-line lead, and its one action. */
function Section({
  title,
  lead,
  action,
  children,
}: {
  title: string
  lead?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-end gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 className="font-heading text-base font-semibold">{title}</h2>
          {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function RowMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button size="icon-sm" variant="ghost" aria-label="More" />}>
        <MoreHorizontalIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuGroup>
          <DropdownMenuItem>Rename</DropdownMenuItem>
          <DropdownMenuItem variant="destructive">Delete</DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/* ---------- Overview: what needs you, then what it can reach ---------- */

function Overview() {
  const pending = LOGINS.filter((l) => l.state !== "signed_in")
  const failed = TOOLSET.mcp.filter((m) => m.state === "failed")
  return (
    <div className="flex flex-col gap-8">
      {(pending.length > 0 || failed.length > 0) && (
        <Section title="Needs you" lead="Things that keep Kin from doing everything it could">
          <ItemGroup className="gap-0 rounded-xl border">
            {pending.map((l) => (
              <Item key={l.id} size="sm">
                <ItemMedia variant="icon">
                  <UserRoundIcon />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>{l.label} is not signed in</ItemTitle>
                  <ItemDescription>{l.description}</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Button size="sm">Sign in</Button>
                </ItemActions>
              </Item>
            ))}
            {failed.map((m) => (
              <Item key={m.name} size="sm">
                <ItemMedia variant="icon">
                  <CircleAlertIcon className="text-destructive" />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>MCP server {m.name} failed</ItemTitle>
                  <ItemDescription>Its {m.tools} tools are missing from every turn</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Button size="sm" variant="outline">
                    Open Tools
                  </Button>
                </ItemActions>
              </Item>
            ))}
          </ItemGroup>
        </Section>
      )}

      <Section title="Views" lead="Pages Kin published">
        <RowList>
          {["Inbox digest", "Trips"].map((v) => (
            <Item key={v} size="sm" render={<button type="button" />}>
              <ItemMedia variant="icon">
                <LayoutPanelLeftIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{v}</ItemTitle>
              </ItemContent>
              <ItemActions>
                <ChevronRightIcon className="text-muted-foreground" />
              </ItemActions>
            </Item>
          ))}
        </RowList>
      </Section>

      <Section
        title="Browser profiles"
        lead="Sign-ins the browser keeps between turns"
        action={
          <Button size="sm" variant="outline">
            <PlusIcon data-icon="inline-start" />
            New profile
          </Button>
        }
      >
        <RowList>
          {PROFILES.map((p) => (
            <Item key={p.name} size="sm">
              <ItemMedia variant="icon">
                <GlobeIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{p.name}</ItemTitle>
                <ItemDescription>{p.site}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <RowMenu />
              </ItemActions>
            </Item>
          ))}
        </RowList>
      </Section>

      <Section
        title="Vault"
        lead="Usernames and passwords Kin fills in. Values are never shown."
        action={
          <Button size="sm" variant="outline">
            <PlusIcon data-icon="inline-start" />
            Add sign-in
          </Button>
        }
      >
        <RowList>
          {VAULT.map((v) => (
            <Item key={v.name} size="sm">
              <ItemMedia variant="icon">
                <KeyRoundIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{v.name}</ItemTitle>
                <ItemDescription>{v.site}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button size="sm" variant="ghost">
                  Edit
                </Button>
                <RowMenu />
              </ItemActions>
            </Item>
          ))}
        </RowList>
      </Section>

      <Section title="Toolset" lead="What a turn can call">
        <RowList>
          <Item size="sm">
            <ItemMedia variant="icon">
              <BrainIcon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>Built in</ItemTitle>
              <ItemDescription>{TOOLSET.builtin.join(", ")}</ItemDescription>
            </ItemContent>
          </Item>
          {TOOLSET.mcp.map((m) => (
            <Item key={m.name} size="sm">
              <ItemMedia variant="icon">
                <ServerIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle className="font-mono">{m.name}</ItemTitle>
                <ItemDescription>{m.tools} tools</ItemDescription>
              </ItemContent>
              <ItemActions>
                {m.state === "failed" ? (
                  <Badge variant="destructive">Failed</Badge>
                ) : (
                  <Badge variant="secondary">Ready</Badge>
                )}
              </ItemActions>
            </Item>
          ))}
        </RowList>
      </Section>
    </div>
  )
}

/** Rows in one bordered list, no gaps: the Settings Rows look. */
function RowList({ children }: { children: React.ReactNode }) {
  return (
    <ItemGroup className="gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none">
      {children}
    </ItemGroup>
  )
}

/* ---------- Config: its section list inside the tab ---------- */

const GROUPS = ["Behavior", "Capabilities", "Instance"] as const

function Config({ life, onLife }: { life: Life; onLife: (l: Life) => void }) {
  const [open, setOpen] = useState(life === "update" ? "core" : "manifest")
  const section = CONFIG_SECTIONS.find((s) => s.id === open) ?? CONFIG_SECTIONS[0]
  const items = CONFIG_SECTIONS.map((s) => ({ value: s.id, label: s.label }))
  const flag = (id: string) =>
    (id === "core" && life === "update") || (id === "tools" && life === "failed")
  return (
    <div className="flex flex-col gap-6 md:flex-row md:gap-8">
      <nav className="hidden w-48 shrink-0 flex-col gap-4 md:flex">
        {GROUPS.map((g) => (
          <div key={g} className="flex flex-col gap-1">
            <span className="px-3 text-xs font-medium text-muted-foreground">{g}</span>
            {CONFIG_SECTIONS.filter((s) => s.group === g).map((s) => (
              <Button
                key={s.id}
                variant={s.id === open ? "secondary" : "ghost"}
                size="sm"
                className="justify-start"
                onClick={() => setOpen(s.id)}
              >
                {s.label}
                {flag(s.id) && (
                  <span
                    aria-label="Needs a look"
                    className="ml-auto size-2 rounded-full bg-ember"
                  />
                )}
              </Button>
            ))}
          </div>
        ))}
      </nav>
      <Select items={items} value={open} onValueChange={(v) => v && setOpen(v)}>
        <SelectTrigger className="w-full md:hidden">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {GROUPS.map((g) => (
            <SelectGroup key={g}>
              <SelectLabel>{g}</SelectLabel>
              {CONFIG_SECTIONS.filter((s) => s.group === g).map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectGroup>
          ))}
        </SelectContent>
      </Select>
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <div className="flex flex-col gap-0.5">
          <h2 className="font-heading text-base font-semibold">{section.label}</h2>
          <p className="text-sm text-muted-foreground">{section.hint}</p>
        </div>
        <ConfigBody id={section.id} life={life} onLife={onLife} />
      </div>
    </div>
  )
}

/* ---------- Routines: one row each, details unfold under it ---------- */

function outcome(r: Routine) {
  if (r.last === undefined) return "never fired"
  const what = {
    work: "did work",
    "no-work": "nothing to do",
    failed: "failed",
    parked: "waiting on an approval",
  }[r.outcome ?? "work"]
  return `last ${r.last}, ${what}`
}

function Routines() {
  const [open, setOpen] = useState<string | undefined>("finance")
  return (
    <Section
      title="Routines"
      lead="What Kin does on a schedule or a signal"
      action={
        <Button size="sm">
          <PlusIcon data-icon="inline-start" />
          New routine
        </Button>
      }
    >
      <RowList>
        {ROUTINES.map((r) => (
          <Collapsible
            key={r.id}
            open={open === r.id}
            onOpenChange={(o) => setOpen(o ? r.id : undefined)}
          >
            <Item size="sm" className={cn(!r.enabled && "opacity-60")}>
              <ItemContent>
                <ItemTitle>
                  <span className="font-mono">{r.name}</span>
                  {r.failures !== undefined && (
                    <Badge variant="destructive">{r.failures} failures in a row</Badge>
                  )}
                  {r.outcome === "parked" && <Badge variant="attention">Needs you</Badge>}
                </ItemTitle>
                <ItemDescription>
                  {r.human}
                  {r.next ? ` · next ${r.next}` : ""} · {outcome(r)}
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <Switch defaultChecked={r.enabled} aria-label={`${r.name} enabled`} />
                <CollapsibleTrigger
                  render={<Button size="icon-sm" variant="ghost" aria-label="Details" />}
                >
                  <ChevronDownIcon
                    className={cn("transition-transform", open === r.id && "rotate-180")}
                  />
                </CollapsibleTrigger>
              </ItemActions>
            </Item>
            <CollapsibleContent>
              <div className="flex flex-col gap-4 border-t bg-muted/40 p-4">
                <p className="text-sm">{r.description}</p>
                <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
                  <dt className="text-muted-foreground">Trigger</dt>
                  <dd className="font-mono">{r.trigger}</dd>
                  <dt className="text-muted-foreground">Mode</dt>
                  <dd>{r.mode}</dd>
                  <dt className="text-muted-foreground">Last changed</dt>
                  <dd>by you, 12 Oct</dd>
                </dl>
                <Field>
                  <FieldLabel className="font-mono">ROUTINE.md</FieldLabel>
                  <Textarea className="min-h-40 font-mono text-sm" defaultValue={ROUTINE_MD} />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline">
                    <PlayIcon data-icon="inline-start" />
                    Run now
                  </Button>
                  <Button size="sm" variant="outline">
                    Rename
                  </Button>
                  <Button size="sm" variant="ghost">
                    Delete
                  </Button>
                  <Button size="sm" className="ml-auto">
                    Save
                  </Button>
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>
        ))}
      </RowList>
    </Section>
  )
}

/* ---------- Memory: the profile as a row, then the graph as rows ---------- */

function Memory() {
  const [profile, setProfile] = useState(false)
  const [open, setOpen] = useState<string | undefined>("f1")
  return (
    <div className="flex flex-col gap-8">
      <Section title="Profile" lead="What Kin knows about you. It goes into every prompt.">
        <RowList>
          <Collapsible open={profile} onOpenChange={setProfile}>
            <Item size="sm">
              <ItemMedia variant="icon">
                <UserRoundIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle className="font-mono">memory/profile.md</ItemTitle>
                <ItemDescription>4 lines · about 60 tokens in every prompt</ItemDescription>
              </ItemContent>
              <ItemActions>
                <CollapsibleTrigger render={<Button size="sm" variant="outline" />}>
                  {profile ? "Close" : "Edit"}
                </CollapsibleTrigger>
              </ItemActions>
            </Item>
            <CollapsibleContent>
              <div className="flex flex-col gap-3 border-t p-4">
                <Textarea className="min-h-36 font-mono text-sm" defaultValue={PROFILE_MD} />
                <div className="flex justify-end">
                  <Button size="sm">Save</Button>
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>
        </RowList>
      </Section>

      <Section
        title="Knowledge"
        lead="214 facts and episodes, newest first"
        action={
          <Button size="sm" variant="outline">
            <PlusIcon data-icon="inline-start" />
            Add fact
          </Button>
        }
      >
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
        <RowList>
          {MEMORY.map((n) => (
            <Collapsible
              key={n.node}
              open={open === n.node}
              onOpenChange={(o) => setOpen(o ? n.node : undefined)}
            >
              <CollapsibleTrigger render={<Item size="sm" render={<button type="button" />} />}>
                <ItemContent>
                  <ItemTitle className="text-left">{n.description}</ItemTitle>
                  <ItemDescription>
                    {n.kind} · {n.date} · {n.subjects.join(", ")}
                    {n.source === "user" && " · added by you"}
                  </ItemDescription>
                </ItemContent>
                <ItemActions>
                  <ChevronDownIcon
                    className={cn(
                      "text-muted-foreground transition-transform",
                      open === n.node && "rotate-180",
                    )}
                  />
                </ItemActions>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="flex flex-col gap-3 border-t bg-muted/40 p-4 text-sm">
                  <p>{MEMORY_BODY}</p>
                  <p className="text-muted-foreground">
                    Recap of a turn in “Plan the Lisbon trip in March” · tool path: web.search →
                    browser.navigate
                  </p>
                  <div className="flex gap-2">
                    {n.kind === "fact" && (
                      <Button size="sm" variant="outline">
                        Correct
                      </Button>
                    )}
                    <Button size="sm" variant="ghost">
                      Forget
                    </Button>
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          ))}
        </RowList>
        <Button variant="ghost" size="sm" className="self-center">
          Load more
        </Button>
      </Section>
    </div>
  )
}

/* ---------- Stats: range on top, tiles, one chart, then the tables ---------- */

function Stats() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center gap-2">
        <RangeToggle />
        <Badge variant="outline">{STATS.plans}</Badge>
        <span className="ml-auto text-xs text-muted-foreground">Daily buckets, UTC</span>
      </div>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border md:grid-cols-4">
        {STATS.tiles.map((t) => (
          <div key={t.label} className="flex flex-col gap-1 bg-background p-4">
            <span className="text-xs text-muted-foreground">{t.label}</span>
            <span className="font-heading text-2xl font-semibold tabular-nums">{t.value}</span>
            <span className="text-xs text-muted-foreground">{t.detail}</span>
          </div>
        ))}
      </div>
      <Section title="Turns per day" lead="Click a bar to see that day's turns">
        <TurnsChart />
      </Section>
      <Section title="Spend" lead="API cost per day, and what each source ran">
        <CostChart />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Runs</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Tokens</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Time</TableHead>
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {STATS.sources.map((s) => (
              <TableRow key={s.source}>
                <TableCell className="font-medium">{s.source}</TableCell>
                <TableCell className="text-right tabular-nums">{s.runs}</TableCell>
                <TableCell className="hidden text-right tabular-nums sm:table-cell">
                  {s.tokens}
                </TableCell>
                <TableCell className="hidden text-right tabular-nums sm:table-cell">
                  {s.time}
                </TableCell>
                <TableCell className="text-right tabular-nums">{s.cost}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
      <Section title="Origin" lead="Where turns came from">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Origin</TableHead>
              <TableHead className="text-right">Turns</TableHead>
              <TableHead className="hidden text-right sm:table-cell">No work</TableHead>
              <TableHead className="text-right">Failed</TableHead>
              <TableHead className="hidden text-right sm:table-cell">API cost</TableHead>
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
                <TableCell className="hidden text-right tabular-nums sm:table-cell">
                  {o.cost}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
      <Section title="Quality" lead="How turns used tools, memory and approvals">
        <div className="grid gap-6 md:grid-cols-3">
          <Figures
            title="Most-called tools"
            rows={STATS.tools.map((t) => [t.tool, t.calls] as const)}
            mono
          />
          <Figures
            title="Memory"
            rows={[
              ["Searches", STATS.memory.searches],
              ["Opens", STATS.memory.opens],
              ["Remembered", STATS.memory.remembered],
              ["Forgotten", STATS.memory.forgotten],
            ]}
          />
          <Figures
            title="Approvals"
            rows={[
              ["Asked", STATS.approvals.asked],
              ["Denied by policy", STATS.approvals.deniedPolicy],
              ["Denied by you", STATS.approvals.deniedYou],
            ]}
          />
        </div>
      </Section>
    </div>
  )
}

function Figures({
  title,
  rows,
  mono,
}: {
  title: string
  rows: readonly (readonly [string, number])[]
  mono?: boolean
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <dl className="flex flex-col divide-y text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between py-1.5">
            <dt className={cn("text-muted-foreground", mono && "font-mono")}>{label}</dt>
            <dd className="tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
