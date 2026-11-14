// PROTOTYPE (#1299), throwaway. Variant C, "Owner-centric": each teammate lists the lines it owns
// under it in the sidebar. The area is a runs inbox, with Lines (by workbench) and Factories as
// other tabs. A line is one long page whose Owner section explains what a change moves. A run's
// page reads like a thread: every attempt in time order, the ask pinned at the bottom. A run that
// needs you shows in the inbox and on its owner's page.
import { cn } from "cn"
import { FactoryIcon, PlusIcon, TriangleAlertIcon } from "lucide-react"
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
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
  runnerKind,
  standing,
} from "./data"
import { go, useAt } from "./nav"
import {
  OutcomeBadge,
  OwnerFrame,
  Page,
  RunBadge,
  Shell,
  StepIcon,
  TeammateAvatar,
  To,
  Transcript,
} from "./shared"

export function VariantC() {
  const [kind, id] = useAt()
  const current = kind === "line" ? id : kind === "run" ? run(id).line : undefined
  return (
    <Shell
      teammates={TEAMMATES}
      extraNav={
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={kind === "area" || kind === "setup"}
            onClick={() => go("area")}
          >
            <FactoryIcon />
            <span>Factories</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      }
      renderTeammate={(who) => {
        const owned = linesOwnedBy(who.id)
        const waiting = owned.flatMap((l) => runsOf(l.id)).filter(needsYou).length
        return (
          <SidebarMenuItem key={who.id}>
            <SidebarMenuButton
              isActive={kind === "owner" && id === who.id}
              onClick={() => go(`owner:${who.id}`)}
            >
              <TeammateAvatar who={who} size="sm" />
              <span>{who.name}</span>
            </SidebarMenuButton>
            {waiting > 0 && (
              <SidebarMenuBadge>
                <Badge variant="attention">{waiting}</Badge>
              </SidebarMenuBadge>
            )}
            {owned.length > 0 && (
              <SidebarMenuSub>
                {owned.map((l) => (
                  <SidebarMenuSubItem key={l.id}>
                    <SidebarMenuSubButton
                      isActive={current === l.id}
                      onClick={() => go(`line:${l.id}`)}
                    >
                      <span className="truncate">{lineName(l)}</span>
                    </SidebarMenuSubButton>
                  </SidebarMenuSubItem>
                ))}
              </SidebarMenuSub>
            )}
          </SidebarMenuItem>
        )
      }}
    >
      {kind === "line" ? (
        <LinePage l={line(id)} />
      ) : kind === "run" ? (
        <RunPage r={run(id)} />
      ) : kind === "owner" ? (
        <OwnerPage ownerId={id} />
      ) : (
        <Area setup={kind === "setup"} />
      )}
    </Shell>
  )
}

function RunRows({ runs }: { runs: Run[] }) {
  return (
    <ul className="flex flex-col divide-y">
      {runs.map((r) => (
        <li key={r.id}>
          <button
            type="button"
            onClick={() => go(`run:${r.id}`)}
            className="flex w-full items-center gap-3 py-2.5 text-left text-sm hover:bg-muted/50"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{r.title}</span>
              <span className="block truncate text-muted-foreground">
                {lineName(line(r.line))} · {r.item} · {standing(r)}
              </span>
            </span>
            <span className="flex flex-col items-end gap-1">
              <RunBadge run={r} />
              <span className="text-xs text-muted-foreground">{r.started}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function Area({ setup }: { setup: boolean }) {
  return (
    <Page
      title="Factories"
      actions={
        <Button onClick={() => go("setup")}>
          <PlusIcon data-icon="inline-start" />
          Set up a line
        </Button>
      }
    >
      <Tabs defaultValue="runs">
        <TabsList variant="line">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="lines">Lines</TabsTrigger>
          <TabsTrigger value="factories">Factories</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <div className="mt-4 flex flex-col gap-6">
            <section>
              <h2 className="text-sm font-medium">Waiting on you</h2>
              <RunRows runs={RUNS.filter(needsYou)} />
            </section>
            <section>
              <h2 className="text-sm font-medium">In flight</h2>
              <RunRows runs={RUNS.filter((r) => unfinished(r) && !needsYou(r))} />
            </section>
            <section>
              <h2 className="text-sm font-medium text-muted-foreground">Finished</h2>
              <RunRows runs={RUNS.filter((r) => !unfinished(r))} />
            </section>
          </div>
        </TabsContent>
        <TabsContent value="lines">
          <div className="mt-4 flex flex-col gap-4">
            {WORKBENCHES.map((w) => (
              <section key={w.id} className="rounded-lg border p-4 text-sm">
                <p className="font-medium">
                  {w.id}{" "}
                  <span className="font-normal text-muted-foreground">
                    {w.image} · {w.source} · {w.state}
                  </span>
                </p>
                {LINES.filter((l) => l.workbench === w.id).map((l) => (
                  <p key={l.id} className="mt-2 flex items-center gap-2">
                    <To at={`line:${l.id}`} className="font-mono">
                      {l.factory}
                    </To>
                    <span className="text-muted-foreground">owned by {teammate(l.owner).name}</span>
                    <span className="ml-auto text-muted-foreground">
                      {runsOf(l.id).filter(unfinished).length} unfinished
                    </span>
                  </p>
                ))}
              </section>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="factories">
          <div className="mt-4 flex flex-col gap-4 text-sm">
            {FACTORIES.map((f) => (
              <section key={f.id}>
                <p className="font-mono font-medium">{f.id}</p>
                <p className="text-muted-foreground">{f.summary}</p>
                <p className="mt-1 flex flex-wrap items-center gap-1 text-xs">
                  {f.steps.map((s) => (
                    <Badge key={s.id} variant="outline">
                      <StepIcon kind={s.kind} />
                      {s.id}
                    </Badge>
                  ))}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Lines:{" "}
                  {LINES.filter((l) => l.factory === f.id)
                    .map((l) => l.workbench)
                    .join(", ")}
                </p>
              </section>
            ))}
          </div>
        </TabsContent>
      </Tabs>
      <SetupSheet open={setup} />
    </Page>
  )
}

function LinePage({ l }: { l: Line }) {
  const [owning, setOwning] = useState(false)
  const owner = teammate(l.owner)
  const w = workbench(l.workbench)
  const f = factory(l.factory)
  const runs = runsOf(l.id)
  return (
    <Page title={lineName(l)} crumbs={<To at="area">Factories</To>}>
      <p className="mb-8 text-sm">
        Runs <span className="font-mono">{f.id}</span> on <b>{w.id}</b> with{" "}
        <span className="font-mono">{l.model}</span>, up to {money(l.budget)} a day (
        {money(l.spentToday)} today). {f.intake}.
      </p>
      <section className="mb-8">
        <h2 className="mb-1 font-medium">Runs</h2>
        <RunRows runs={runs} />
      </section>
      <section className="mb-8 text-sm">
        <h2 className="mb-2 font-medium">Workbench</h2>
        <p className="text-muted-foreground">
          {w.image} image · {w.source} · {w.state} · picks {w.picks.join(", ") || "none"}
        </p>
        {Object.entries(w.commands).map(([name, cmds]) => (
          <p key={name} className="mt-1">
            <span className="text-muted-foreground">{name}: </span>
            <code className="text-xs">{cmds.join(" · ")}</code>
          </p>
        ))}
        <p className="mt-2">
          Meets what {f.id} requires:{" "}
          {[...missing(f, w).held, `${f.requires.image} image`].join(", ")}.
        </p>
      </section>
      <section className="text-sm">
        <h2 className="mb-2 font-medium">Owner</h2>
        <div className="flex items-center gap-3 rounded-lg border p-3">
          <TeammateAvatar who={owner} />
          <div className="flex-1">
            <To at={`owner:${owner.id}`} className="font-medium">
              {owner.name}
            </To>
            <p className="text-muted-foreground">
              Gets this line's run stops, keeps its intake routine and remembers what its runs
              learn, marked {w.id}.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setOwning(true)}>
            Change owner…
          </Button>
        </div>
      </section>
      <Dialog open={owning} onOpenChange={setOwning}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move {lineName(l)} to another owner</DialogTitle>
            <DialogDescription>This moves, in one operation:</DialogDescription>
          </DialogHeader>
          <ul className="list-disc pl-5 text-sm">
            <li>
              {runs.length} runs, {runs.filter(unfinished).length} unfinished
            </li>
            <li>the intake routine and its webhook address</li>
            <li>38 facts and 12 episodes marked {w.id}</li>
          </ul>
          {runs.some((r) => r.status === "running") && (
            <Alert>
              <TriangleAlertIcon />
              <AlertTitle>A run is running a step</AlertTitle>
              <AlertDescription>The move waits until no step runs.</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            {TEAMMATES.filter((t) => t.id !== l.owner).map((t) => (
              <Button key={t.id} onClick={() => setOwning(false)}>
                Move to {t.name}
              </Button>
            ))}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  )
}

function RunPage({ r }: { r: Run }) {
  const l = line(r.line)
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 py-6 md:px-8">
      <p className="text-sm text-muted-foreground">
        <To at={`line:${l.id}`}>{lineName(l)}</To> · {r.item}
      </p>
      <h1 className="font-heading text-2xl font-semibold">{r.title}</h1>
      <p className="mt-1 mb-6 flex items-center gap-2 text-sm text-muted-foreground">
        <RunBadge run={r} /> {standing(r)} · started {r.started} · {money(r.cost)}
      </p>
      {r.attempts.length === 0 && (
        <p className="text-sm text-muted-foreground">No attempt to show in this prototype.</p>
      )}
      <ol className="relative flex flex-col gap-5 border-l pl-6">
        {r.attempts.map((a, index) => (
          <TimelineAttempt key={index} a={a} last={index === r.attempts.length - 1} />
        ))}
      </ol>
      {r.waiting !== undefined && (
        <div className="sticky bottom-20 mt-8 rounded-xl border border-ember bg-background p-4 shadow-lg">
          <p className="mb-3 text-sm">
            <b>
              {r.waiting.kind === "approval"
                ? "Approve to go on"
                : r.waiting.kind === "takeover"
                  ? "Take over the browser"
                  : "This run needs you"}
            </b>
            <span className="block text-muted-foreground">
              {r.waiting.kind === "approval"
                ? r.waiting.summary
                : r.waiting.kind === "takeover"
                  ? r.waiting.reason
                  : r.waiting.reason}
            </span>
          </p>
          {r.waiting.kind !== "takeover" && (
            <Input className="mb-2" placeholder="Notes for the step it goes back to (optional)" />
          )}
          <div className="flex flex-wrap gap-2">
            {r.waiting.kind === "approval" && <Button>Approve</Button>}
            {r.waiting.kind === "takeover" && <Button>Take over</Button>}
            {r.waiting.kind === "needs-human" && <Button>Retry {r.step}</Button>}
            {r.waiting.kind !== "takeover" && <Button variant="outline">Send back to…</Button>}
            <Button variant="ghost">Cancel run</Button>
          </div>
        </div>
      )}
    </div>
  )
}

function TimelineAttempt({ a, last }: { a: Run["attempts"][number]; last: boolean }) {
  const [open, setOpen] = useState(last || a.outcome === "failed" || a.outcome === "needs human")
  return (
    <li className="relative">
      <span
        className={cn(
          "absolute top-0.5 -left-9 flex size-6 items-center justify-center rounded-full border bg-background",
          a.outcome === "failed" && "border-destructive text-destructive",
          (a.outcome === "waiting" || a.outcome === "asked" || a.outcome === "needs human") &&
            "border-ember text-ember",
        )}
      >
        <StepIcon kind={runnerKind(a)} className="size-3" />
      </span>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono font-medium">{a.step}</span>
        {a.n > 1 && <span className="text-muted-foreground">#{a.n}</span>}
        <span className="text-muted-foreground">
          {a.runner}
          {a.session ? ` ${a.session}` : ""} · {a.started} · {a.took}
          {a.cost !== undefined ? ` · ${money(a.cost)}` : ""}
        </span>
        <OutcomeBadge outcome={a.outcome} />
      </div>
      <p className="mt-1 text-sm">{a.summary}</p>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="mt-1 text-xs text-muted-foreground underline-offset-4 hover:underline"
      >
        {open ? "Hide transcript" : "Show transcript"}
      </button>
      {open && (
        <div className="mt-2 rounded-lg bg-muted/40 p-3">
          <Transcript entries={a.transcript} />
        </div>
      )}
    </li>
  )
}

function OwnerPage({ ownerId }: { ownerId: string }) {
  const who = teammate(ownerId)
  const owned = linesOwnedBy(ownerId)
  const waiting = owned.flatMap((l) => runsOf(l.id)).filter(needsYou)
  return (
    <OwnerFrame who={who}>
      {waiting.length > 0 && (
        <section className="rounded-lg border border-ember p-4">
          <h2 className="mb-1 text-sm font-medium">Runs waiting on you</h2>
          <RunRows runs={waiting} />
        </section>
      )}
      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Lines {who.name} owns</h2>
        {owned.length === 0 && <p className="text-sm text-muted-foreground">None.</p>}
        <ul className="grid gap-2 md:grid-cols-2">
          {owned.map((l) => (
            <li key={l.id} className="rounded-lg border p-3 text-sm">
              <To at={`line:${l.id}`} className="font-medium">
                {lineName(l)}
              </To>
              <p className="text-muted-foreground">
                {runsOf(l.id).filter(unfinished).length} unfinished · {money(l.spentToday)} today
              </p>
            </li>
          ))}
        </ul>
      </section>
    </OwnerFrame>
  )
}

/** Set up a line in one pass, in a sheet: both picks on top, then each requirement held or asked. */
function SetupSheet({ open }: { open: boolean }) {
  const [factoryId, setFactoryId] = useState("software")
  const [benchId, setBenchId] = useState("new")
  const f = factory(factoryId)
  const w = benchId === "new" ? undefined : workbench(benchId)
  const gap = missing(f, w)
  const r = f.requires
  const holds = (asked: boolean, label: string, input: React.ReactNode) => (
    <Field key={label}>
      <FieldLabel>{label}</FieldLabel>
      {asked ? input : <FieldDescription>✓ {w?.id} holds it</FieldDescription>}
    </Field>
  )
  return (
    <Sheet open={open} onOpenChange={(next) => !next && go("area")}>
      <SheetContent className="overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Set up a line</SheetTitle>
          <SheetDescription>One submission. Owned by Home; change it later.</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-6">
          <FieldGroup>
            <Field>
              <FieldLabel>Factory</FieldLabel>
              <Select
                items={FACTORIES.map((option) => ({ value: option.id, label: option.id }))}
                value={factoryId}
                onValueChange={(next) => next !== null && setFactoryId(next)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FACTORIES.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel>Workbench</FieldLabel>
              <Select
                items={[
                  { value: "new", label: "New workbench" },
                  ...WORKBENCHES.map((option) => ({ value: option.id, label: option.id })),
                ]}
                value={benchId}
                onValueChange={(next) => next !== null && setBenchId(next)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new">New workbench</SelectItem>
                  {WORKBENCHES.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {gap.blockers.map((b) => (
              <Alert key={b} variant="destructive">
                <TriangleAlertIcon />
                <AlertTitle>Cannot take this line</AlertTitle>
                <AlertDescription>{b}</AlertDescription>
              </Alert>
            ))}
            {gap.blockers.length === 0 && (
              <>
                {w === undefined && (
                  <>
                    <Field>
                      <FieldLabel>Name</FieldLabel>
                      <Input placeholder="my-repo" />
                    </Field>
                    <Field>
                      <FieldLabel>Workspace</FieldLabel>
                      <Input placeholder="git URL, or empty for a plain folder" />
                      <FieldDescription>
                        Built from the {r.image} image{r.browser ? ", with a browser" : ""}.
                      </FieldDescription>
                    </Field>
                  </>
                )}
                {r.secrets.map((s) =>
                  holds(
                    gap.secrets.includes(s),
                    s.label,
                    <Input type="password" placeholder={s.name} />,
                  ),
                )}
                {r.values.map((v) =>
                  holds(gap.values.includes(v), v.label, <Input placeholder={v.name} />),
                )}
                {r.commands.map((c) =>
                  holds(
                    gap.commands.includes(c),
                    `${c} commands`,
                    <Input placeholder="One per line" />,
                  ),
                )}
                <Button onClick={() => go("area")}>Set up</Button>
              </>
            )}
          </FieldGroup>
        </div>
      </SheetContent>
    </Sheet>
  )
}
