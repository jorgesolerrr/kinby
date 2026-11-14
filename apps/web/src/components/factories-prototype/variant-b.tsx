// PROTOTYPE (#1299), throwaway. Variant B, "Lines in the sidebar": every line sits in the sidebar
// under the teammates, with its unfinished runs counted. The area is a board of runs by what they
// wait on. A line's page puts its runs beside its settings, with the owner as an inline control. A
// run's page is a rail of the factory's steps; picking one shows its attempts. A run that needs you
// shows in the Factories area only; the owner's page just links its lines.
import { cn } from "cn"
import { CheckIcon, CircleIcon, PlusIcon, RotateCcwIcon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import {
  FACTORIES,
  factory,
  line,
  LINES,
  lineName,
  linesOwnedBy,
  missing,
  needsYou,
  run,
  RUNS,
  runsOf,
  teammate,
  TEAMMATES,
  unfinished,
  workbench,
  WORKBENCHES,
  type Line,
  type Run,
  money,
  standing,
} from "./data"
import { go, useAt } from "./nav"
import { OutcomeBadge, OwnerFrame, Page, RunBadge, Shell, StepIcon, To, Transcript } from "./shared"

export function VariantB() {
  const [kind, id] = useAt()
  const current = kind === "line" ? id : kind === "run" ? run(id).line : undefined
  return (
    <Shell
      teammates={TEAMMATES}
      belowTeammates={
        <SidebarGroup>
          <SidebarGroupLabel
            render={<button type="button" aria-label="Lines" onClick={() => go("area")} />}
            className="cursor-pointer"
          >
            Lines
          </SidebarGroupLabel>
          <SidebarGroupAction title="Set up a line" onClick={() => go("setup")}>
            <PlusIcon />
          </SidebarGroupAction>
          <SidebarMenu>
            {LINES.map((l) => {
              const open = runsOf(l.id).filter(unfinished)
              const waits = open.some(needsYou)
              return (
                <SidebarMenuItem key={l.id}>
                  <SidebarMenuButton isActive={current === l.id} onClick={() => go(`line:${l.id}`)}>
                    <CircleIcon
                      className={cn(
                        "size-2!",
                        waits ? "fill-ember text-ember" : "text-muted-foreground",
                      )}
                    />
                    <span className="truncate">
                      <span className="text-muted-foreground">{l.factory} · </span>
                      {l.workbench}
                    </span>
                  </SidebarMenuButton>
                  {open.length > 0 && <SidebarMenuBadge>{open.length}</SidebarMenuBadge>}
                </SidebarMenuItem>
              )
            })}
          </SidebarMenu>
        </SidebarGroup>
      }
    >
      {kind === "line" ? (
        <LinePage l={line(id)} />
      ) : kind === "run" ? (
        <RunPage r={run(id)} />
      ) : kind === "owner" ? (
        <OwnerPage ownerId={id} />
      ) : kind === "setup" ? (
        <Setup />
      ) : (
        <Board />
      )}
    </Shell>
  )
}

const COLUMNS: { title: string; holds: (r: Run) => boolean }[] = [
  { title: "Needs you", holds: needsYou },
  { title: "Running", holds: (r) => r.status === "running" },
  {
    title: "Waiting",
    holds: (r) => !needsYou(r) && (r.status === "parked" || r.status === "queued"),
  },
]

function Board() {
  const [only, setOnly] = useState<string>()
  const runs = RUNS.filter((r) => only === undefined || line(r.line).factory === only)
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-8">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-4 font-heading text-2xl font-semibold">Factories</h1>
        <Button
          size="sm"
          variant={only === undefined ? "secondary" : "ghost"}
          onClick={() => setOnly(undefined)}
        >
          All
        </Button>
        {FACTORIES.map((f) => (
          <Button
            key={f.id}
            size="sm"
            variant={only === f.id ? "secondary" : "ghost"}
            onClick={() => setOnly(f.id)}
          >
            <span className="font-mono">{f.id}</span>
            <span className="text-muted-foreground">
              {LINES.filter((l) => l.factory === f.id).length}
            </span>
          </Button>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {COLUMNS.map((column) => {
          const held = runs.filter(column.holds)
          return (
            <section key={column.title} className="flex flex-col gap-2 rounded-xl bg-muted/50 p-3">
              <h2 className="flex items-center justify-between text-sm font-medium">
                {column.title}
                <span className="text-muted-foreground">{held.length}</span>
              </h2>
              {held.map((r) => (
                <button
                  type="button"
                  key={r.id}
                  onClick={() => go(`run:${r.id}`)}
                  className="flex flex-col gap-1 rounded-lg border bg-background p-3 text-left text-sm hover:bg-muted"
                >
                  <span className="font-medium">{r.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {lineName(line(r.line))} · {r.item}
                  </span>
                  <span className="flex items-center gap-2">
                    <RunBadge run={r} />
                    <span className="text-xs text-muted-foreground">{r.step ?? ""}</span>
                  </span>
                </button>
              ))}
            </section>
          )
        })}
      </div>
      <p className="text-sm text-muted-foreground">Finished runs live on each line's page.</p>
    </div>
  )
}

function LinePage({ l }: { l: Line }) {
  const [owner, setOwner] = useState(l.owner)
  const w = workbench(l.workbench)
  const f = factory(l.factory)
  return (
    <div className="grid gap-8 px-4 py-6 md:grid-cols-3 md:px-8">
      <div className="md:col-span-2">
        <p className="font-mono text-sm text-muted-foreground">{l.factory}</p>
        <h1 className="mb-6 font-heading text-2xl font-semibold">{l.workbench}</h1>
        <ul className="flex flex-col divide-y">
          {runsOf(l.id).map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => go(`run:${r.id}`)}
                className="flex w-full items-center gap-3 py-3 text-left text-sm hover:bg-muted/50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{r.title}</span>
                  <span className="text-muted-foreground">
                    {r.item} · {r.started}
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">{r.step}</span>
                <RunBadge run={r} />
              </button>
            </li>
          ))}
        </ul>
      </div>
      <aside className="flex flex-col gap-5 text-sm md:border-l md:pl-6">
        <Field>
          <FieldLabel>Owner</FieldLabel>
          <Select
            items={TEAMMATES.map((t) => ({ value: t.id, label: t.name }))}
            value={owner}
            onValueChange={(next) => next !== null && setOwner(next)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TEAMMATES.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {owner !== l.owner && (
            <FieldDescription>
              Moves runs, intake and the memory marked {l.workbench} to {teammate(owner).name}.{" "}
              <Button size="xs" onClick={() => setOwner(owner)}>
                Move
              </Button>
            </FieldDescription>
          )}
        </Field>
        <div>
          <p className="text-muted-foreground">Model</p>
          <p className="font-mono text-xs">{l.model}</p>
        </div>
        <div>
          <p className="mb-1 text-muted-foreground">
            Today {money(l.spentToday)} of {money(l.budget)}
          </p>
          <Progress value={(l.spentToday / l.budget) * 100} />
        </div>
        <div>
          <p className="text-muted-foreground">Intake</p>
          <p>{f.intake}</p>
        </div>
        <hr />
        <div>
          <p className="font-medium">Workbench {w.id}</p>
          <p className="text-muted-foreground">
            {w.image} · {w.state}
          </p>
          <p className="font-mono text-xs break-all">{w.source}</p>
        </div>
        {Object.entries(w.commands).map(([name, cmds]) => (
          <div key={name}>
            <p className="text-muted-foreground">{name}</p>
            <p className="font-mono text-xs">{cmds.join(" && ")}</p>
          </div>
        ))}
        <div>
          <p className="text-muted-foreground">Meets {f.id}</p>
          {missing(f, w).held.map((h) => (
            <p key={h} className="flex items-center gap-1">
              <CheckIcon className="size-3 text-success" /> {h}
            </p>
          ))}
        </div>
      </aside>
    </div>
  )
}

function RunPage({ r }: { r: Run }) {
  const l = line(r.line)
  const f = factory(l.factory)
  const [picked, setPicked] = useState(r.step ?? r.attempts.at(-1)?.step ?? f.steps[0].id)
  const attempts = r.attempts.filter((a) => a.step === picked)
  return (
    <div className="flex flex-col gap-6 px-4 py-6 md:px-8">
      <div>
        <p className="text-sm text-muted-foreground">
          <To at={`line:${l.id}`}>{lineName(l)}</To> · {r.item}
        </p>
        <h1 className="font-heading text-2xl font-semibold">{r.title}</h1>
        <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
          <RunBadge run={r} /> {standing(r)} · {money(r.cost)}
        </p>
      </div>
      <ol className="flex gap-1 overflow-x-auto pb-2">
        {f.steps.map((s, index) => {
          const tries = r.attempts.filter((a) => a.step === s.id)
          const last = tries.at(-1)
          return (
            <li key={s.id} className="flex items-center gap-1">
              {index > 0 && <span className="text-muted-foreground">→</span>}
              <button
                type="button"
                onClick={() => setPicked(s.id)}
                className={cn(
                  "flex min-w-24 flex-col items-start gap-1 rounded-lg border px-3 py-2 text-left text-xs",
                  picked === s.id && "border-foreground",
                  r.step === s.id && r.waiting && "border-ember bg-ember/10",
                  tries.length === 0 && "opacity-50",
                )}
              >
                <span className="flex items-center gap-1 font-medium">
                  <StepIcon kind={s.kind} className="size-3" />
                  {s.id}
                </span>
                <span className="text-muted-foreground">
                  {tries.length === 0
                    ? "—"
                    : tries.length > 1
                      ? `×${tries.length} · ${last?.outcome}`
                      : last?.outcome}
                </span>
                {tries.length > 1 && (
                  <RotateCcwIcon className="size-3 text-muted-foreground" aria-label="sent back" />
                )}
              </button>
            </li>
          )
        })}
      </ol>
      {r.waiting !== undefined && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg bg-ember/10 px-4 py-3 text-sm">
          <span className="flex-1">
            {r.waiting.kind === "approval"
              ? r.waiting.summary
              : r.waiting.kind === "takeover"
                ? r.waiting.reason
                : r.waiting.reason}
          </span>
          {r.waiting.kind === "approval" && (
            <>
              <Button size="sm">Approve</Button>
              <Button size="sm" variant="outline">
                Send back
              </Button>
            </>
          )}
          {r.waiting.kind === "takeover" && <Button size="sm">Take over</Button>}
          {r.waiting.kind === "needs-human" && (
            <>
              <Button size="sm">Retry</Button>
              <Button size="sm" variant="outline">
                Send back
              </Button>
              <Button size="sm" variant="ghost">
                Cancel
              </Button>
            </>
          )}
        </div>
      )}
      {attempts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{picked} has no attempt yet.</p>
      ) : (
        <Tabs key={picked} defaultValue={String(attempts.at(-1)!.n)}>
          <TabsList>
            {attempts.map((a) => (
              <TabsTrigger key={a.n} value={String(a.n)}>
                Attempt {a.n}
              </TabsTrigger>
            ))}
          </TabsList>
          {attempts.map((a) => (
            <TabsContent key={a.n} value={String(a.n)}>
              <div className="mt-4 mb-3 flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="outline">{a.runner}</Badge>
                {a.session && <span className="font-mono text-xs">{a.session}</span>}
                <OutcomeBadge outcome={a.outcome} />
                <span className="text-muted-foreground">
                  {a.started} · {a.took}
                  {a.cost !== undefined ? ` · ${money(a.cost)}` : ""}
                </span>
              </div>
              <p className="mb-3 text-sm font-medium">{a.summary}</p>
              <Transcript entries={a.transcript} />
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  )
}

function OwnerPage({ ownerId }: { ownerId: string }) {
  const who = teammate(ownerId)
  const owned = linesOwnedBy(ownerId)
  return (
    <OwnerFrame who={who}>
      <p className="text-sm">
        <span className="text-muted-foreground">Owns </span>
        {owned.length === 0
          ? "no line"
          : owned.map((l, index) => (
              <span key={l.id}>
                {index > 0 && ", "}
                <To at={`line:${l.id}`}>{lineName(l)}</To>
              </span>
            ))}
      </p>
    </OwnerFrame>
  )
}

/** Set up a line on a page of its own, three columns filled left to right. */
function Setup() {
  const [factoryId, setFactoryId] = useState<string>()
  const [benchId, setBenchId] = useState<string | null>()
  const f = factoryId === undefined ? undefined : factory(factoryId)
  const w = benchId ? workbench(benchId) : undefined
  const gap = f === undefined || benchId === undefined ? undefined : missing(f, w)
  return (
    <Page title="Set up a line">
      <div className="grid gap-6 md:grid-cols-3">
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-medium text-muted-foreground">1. Factory</h2>
          {FACTORIES.map((option) => (
            <button
              type="button"
              key={option.id}
              onClick={() => {
                setFactoryId(option.id)
                setBenchId(undefined)
              }}
              className={cn(
                "rounded-lg border p-3 text-left text-sm",
                factoryId === option.id && "border-foreground",
              )}
            >
              <span className="font-mono font-medium">{option.id}</span>
              <span className="block text-muted-foreground">{option.summary}</span>
            </button>
          ))}
        </section>
        <section className={cn("flex flex-col gap-2", f === undefined && "opacity-40")}>
          <h2 className="text-sm font-medium text-muted-foreground">2. Workbench</h2>
          {f !== undefined &&
            WORKBENCHES.map((option) => {
              const fit = missing(f, option)
              const asks = fit.secrets.length + fit.values.length + fit.commands.length
              return (
                <button
                  type="button"
                  key={option.id}
                  disabled={fit.blockers.length > 0}
                  onClick={() => setBenchId(option.id)}
                  className={cn(
                    "rounded-lg border p-3 text-left text-sm disabled:opacity-40",
                    benchId === option.id && "border-foreground",
                  )}
                >
                  <span className="font-medium">{option.id}</span>
                  <span className="block text-muted-foreground">
                    {fit.blockers[0] ?? (asks === 0 ? "Holds everything" : `Needs ${asks} answers`)}
                  </span>
                </button>
              )
            })}
          {f !== undefined && (
            <Button
              variant={benchId === null ? "secondary" : "outline"}
              onClick={() => setBenchId(null)}
            >
              <PlusIcon data-icon="inline-start" /> New workbench
            </Button>
          )}
        </section>
        <section className={cn("flex flex-col gap-2", gap === undefined && "opacity-40")}>
          <h2 className="text-sm font-medium text-muted-foreground">3. What's missing</h2>
          {gap !== undefined && f !== undefined && (
            <FieldGroup>
              {w === undefined && (
                <>
                  <Field>
                    <FieldLabel>Name</FieldLabel>
                    <Input placeholder="my-repo" />
                  </Field>
                  <Field>
                    <FieldLabel>Workspace</FieldLabel>
                    <Input placeholder="git URL or empty" />
                  </Field>
                </>
              )}
              {gap.secrets.map((s) => (
                <Field key={s.name}>
                  <FieldLabel>{s.label}</FieldLabel>
                  <Input type="password" />
                </Field>
              ))}
              {gap.values.map((v) => (
                <Field key={v.name}>
                  <FieldLabel>{v.label}</FieldLabel>
                  <Input />
                </Field>
              ))}
              {gap.commands.map((c) => (
                <Field key={c}>
                  <FieldLabel>{c} commands</FieldLabel>
                  <Input />
                </Field>
              ))}
              {gap.held.length > 0 && (
                <FieldDescription>
                  Already on {w?.id}: {gap.held.join(", ")}.
                </FieldDescription>
              )}
              <Button onClick={() => go("area")}>Set up {f.id}</Button>
            </FieldGroup>
          )}
        </section>
      </div>
    </Page>
  )
}
