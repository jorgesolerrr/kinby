// PROTOTYPE (#1299), throwaway. Variant A, "Directory": Factories sits beside Library in the
// sidebar's menu. The area lists each factory as a card with a table of its lines. A line's page has
// Runs and Workbench tabs, and its owner changes behind a menu. A run's page groups attempts under
// each step in the factory's order. A run that needs you shows in the area and on its owner.
import { CircleAlertIcon, FactoryIcon, MoreHorizontalIcon, PlusIcon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
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

export function VariantA() {
  const [kind, id] = useAt()
  const waiting = RUNS.filter(needsYou).length
  return (
    <Shell
      teammates={TEAMMATES}
      extraNav={
        <SidebarMenuItem>
          <SidebarMenuButton isActive={kind !== "owner"} onClick={() => go("area")}>
            <FactoryIcon />
            <span>Factories</span>
          </SidebarMenuButton>
          <SidebarMenuBadge>
            <Badge variant="attention">{waiting}</Badge>
          </SidebarMenuBadge>
        </SidebarMenuItem>
      }
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

function Area({ setup }: { setup: boolean }) {
  const waiting = RUNS.filter(needsYou)
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
      {waiting.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium">
            <CircleAlertIcon className="size-4 text-ember" />
            Waiting on you
          </h2>
          <ul className="flex flex-col divide-y rounded-lg border">
            {waiting.map((r) => (
              <li
                key={r.id}
                className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm"
              >
                <div className="min-w-0">
                  <To at={`run:${r.id}`} className="font-medium">
                    {r.title}
                  </To>
                  <p className="truncate text-muted-foreground">
                    {lineName(line(r.line))} ·{" "}
                    {r.waiting?.kind === "approval"
                      ? r.waiting.summary
                      : r.waiting?.kind === "takeover"
                        ? r.waiting.reason
                        : r.waiting?.reason}
                  </p>
                </div>
                <RunBadge run={r} />
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="flex flex-col gap-6">
        {FACTORIES.map((f) => (
          <Card key={f.id}>
            <CardHeader>
              <CardTitle>
                <span className="font-mono">{f.id}</span>
              </CardTitle>
              <CardDescription>{f.summary}</CardDescription>
              <CardAction>
                <span className="text-xs text-muted-foreground">
                  {f.steps.map((s) => s.id).join(" → ")}
                </span>
              </CardAction>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Workbench</TableHead>
                    <TableHead>Owner</TableHead>
                    <TableHead>Unfinished runs</TableHead>
                    <TableHead className="text-right">Today</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {LINES.filter((l) => l.factory === f.id).map((l) => (
                    <LineRow key={l.id} l={l} />
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>
      <SetupDialog open={setup} />
    </Page>
  )
}

function LineRow({ l }: { l: Line }) {
  const runs = runsOf(l.id).filter(unfinished)
  const owner = teammate(l.owner)
  return (
    <TableRow className="cursor-pointer" onClick={() => go(`line:${l.id}`)}>
      <TableCell>
        <span className="font-medium">{l.workbench}</span>
      </TableCell>
      <TableCell>
        <span className="flex items-center gap-2">
          <TeammateAvatar who={owner} size="sm" />
          {owner.name}
        </span>
      </TableCell>
      <TableCell>
        <span className="flex flex-wrap gap-1">
          {runs.length === 0 && <span className="text-muted-foreground">None</span>}
          {runs.map((r) => (
            <RunBadge key={r.id} run={r} />
          ))}
        </span>
      </TableCell>
      <TableCell className="text-right">
        <span className="tabular-nums">
          {money(l.spentToday)} / {money(l.budget)}
        </span>
      </TableCell>
    </TableRow>
  )
}

function LinePage({ l }: { l: Line }) {
  const [owning, setOwning] = useState(false)
  const owner = teammate(l.owner)
  const w = workbench(l.workbench)
  const f = factory(l.factory)
  return (
    <Page
      crumbs={
        <>
          <To at="area">Factories</To> / {l.factory}
        </>
      }
      title={lineName(l)}
      actions={
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="icon" aria-label="More" />}>
            <MoreHorizontalIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setOwning(true)}>Change owner…</DropdownMenuItem>
            <DropdownMenuItem>Change model…</DropdownMenuItem>
            <DropdownMenuItem>Change daily budget…</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive">Remove line</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
    >
      <dl className="mb-6 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Owner</dt>
          <dd>
            <To at={`owner:${owner.id}`} className="flex items-center gap-2">
              <TeammateAvatar who={owner} size="sm" /> {owner.name}
            </To>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Model</dt>
          <dd className="font-mono text-xs">{l.model}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Daily budget</dt>
          <dd className="tabular-nums">
            {money(l.spentToday)} of {money(l.budget)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Intake</dt>
          <dd>{f.intake}</dd>
        </div>
      </dl>
      <Tabs defaultValue="runs">
        <TabsList variant="line">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="workbench">Workbench</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <Table className="mt-4">
            <TableHeader>
              <TableRow>
                <TableHead>Work item</TableHead>
                <TableHead>Where</TableHead>
                <TableHead>Started</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runsOf(l.id).map((r) => (
                <TableRow key={r.id} className="cursor-pointer" onClick={() => go(`run:${r.id}`)}>
                  <TableCell>
                    <p className="font-medium">{r.title}</p>
                    <p className="text-muted-foreground">{r.item}</p>
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      <RunBadge run={r} />
                      {r.step}
                    </span>
                  </TableCell>
                  <TableCell>{r.started}</TableCell>
                  <TableCell className="text-right">
                    <span className="tabular-nums">{money(r.cost)}</span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TabsContent>
        <TabsContent value="workbench">
          <div className="mt-4 flex flex-col gap-6 text-sm">
            <dl className="grid grid-cols-2 gap-4">
              <div>
                <dt className="text-muted-foreground">Workbench</dt>
                <dd className="font-medium">{w.id}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">State</dt>
                <dd>{w.state}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Image</dt>
                <dd className="font-mono text-xs">{w.image}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Workspace</dt>
                <dd className="font-mono text-xs">{w.source}</dd>
              </div>
            </dl>
            <section>
              <h3 className="mb-2 font-medium">Repository commands</h3>
              {Object.keys(w.commands).length === 0 && (
                <p className="text-muted-foreground">None held.</p>
              )}
              {Object.entries(w.commands).map(([name, cmds]) => (
                <div key={name} className="mb-2">
                  <p className="text-muted-foreground">{name}</p>
                  <pre className="rounded-md bg-muted p-2 font-mono text-xs">{cmds.join("\n")}</pre>
                </div>
              ))}
            </section>
            <section>
              <h3 className="mb-2 font-medium">What {f.id} requires</h3>
              <ul className="flex flex-col gap-1">
                {missing(f, w).held.map((h) => (
                  <li key={h}>✓ {h}</li>
                ))}
                <li>✓ {f.requires.image} image</li>
                {f.requires.browser && <li>✓ Browser</li>}
              </ul>
            </section>
            <section>
              <h3 className="mb-2 font-medium">Other lines on {w.id}</h3>
              <p className="text-muted-foreground">
                {LINES.filter((o) => o.workbench === w.id && o.id !== l.id)
                  .map(lineName)
                  .join(", ") || "None"}
              </p>
            </section>
          </div>
        </TabsContent>
      </Tabs>
      <ChangeOwner l={l} open={owning} onOpenChange={setOwning} />
    </Page>
  )
}

function ChangeOwner({
  l,
  open,
  onOpenChange,
}: {
  l: Line
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [to, setTo] = useState(l.owner)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change the owner of {lineName(l)}</DialogTitle>
          <DialogDescription>
            Its runs, its intake routine and the memory marked {l.workbench} move to the new owner.
            Refused while a run is running a step.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          {TEAMMATES.map((who) => (
            <Button
              key={who.id}
              variant={to === who.id ? "secondary" : "ghost"}
              className="justify-start"
              onClick={() => setTo(who.id)}
            >
              <TeammateAvatar who={who} size="sm" />
              {who.name}
              {who.id === l.owner && " (now)"}
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button disabled={to === l.owner} onClick={() => onOpenChange(false)}>
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RunPage({ r }: { r: Run }) {
  const l = line(r.line)
  const f = factory(l.factory)
  return (
    <Page
      crumbs={
        <>
          <To at="area">Factories</To> / <To at={`line:${l.id}`}>{lineName(l)}</To>
        </>
      }
      title={r.title}
    >
      <p className="mb-6 flex items-center gap-2 text-sm text-muted-foreground">
        <RunBadge run={r} /> {r.item} · started {r.started} · {money(r.cost)}
      </p>
      {r.waiting !== undefined && <WaitingCard r={r} />}
      <ol className="flex flex-col gap-2">
        {f.steps.map((s) => {
          const attempts = r.attempts.filter((a) => a.step === s.id)
          const current = r.step === s.id
          return (
            <li key={s.id}>
              <Collapsible defaultOpen={current}>
                <CollapsibleTrigger
                  disabled={attempts.length === 0}
                  render={
                    <button
                      type="button"
                      aria-label={s.id}
                      className="flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left text-sm disabled:opacity-50"
                    />
                  }
                >
                  <StepIcon kind={s.kind} />
                  <span className="font-medium">{s.id}</span>
                  <span className="text-muted-foreground">
                    {s.kind}
                    {s.detail ? ` · ${s.detail}` : ""}
                  </span>
                  <span className="ml-auto text-muted-foreground">
                    {attempts.length === 0
                      ? "not reached"
                      : `${attempts.length} attempt${attempts.length > 1 ? "s" : ""}`}
                  </span>
                  {attempts.length > 0 && <OutcomeBadge outcome={attempts.at(-1)!.outcome} />}
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="ml-6 flex flex-col gap-4 border-l-2 py-3 pl-6">
                    {attempts.map((a) => (
                      <div key={`${a.step}${a.n}`}>
                        <p className="mb-1 text-xs text-muted-foreground">
                          Attempt {a.n} · {a.runner}
                          {a.session ? ` (${a.session})` : ""} · {a.started} · {a.took}
                          {a.cost !== undefined ? ` · ${money(a.cost)}` : ""}
                        </p>
                        <p className="mb-2 text-sm font-medium">{a.summary}</p>
                        <Transcript entries={a.transcript} />
                      </div>
                    ))}
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </li>
          )
        })}
      </ol>
    </Page>
  )
}

function WaitingCard({ r }: { r: Run }) {
  const w = r.waiting!
  return (
    <Card variant="attention" className="mb-6">
      <CardHeader>
        <CardTitle>
          {w.kind === "approval"
            ? `${r.step} waits for your approval`
            : w.kind === "takeover"
              ? `${r.step} needs you to take over the browser`
              : `Needs a human at ${r.step}`}
        </CardTitle>
        <CardDescription>
          {w.kind === "approval" ? w.summary : w.kind === "takeover" ? w.reason : w.reason}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {w.kind === "approval" && (
            <>
              <Button>Approve</Button>
              <Button variant="outline">Send back…</Button>
            </>
          )}
          {w.kind === "takeover" && <Button>Take over</Button>}
          {w.kind === "needs-human" && (
            <>
              <Button>Retry {r.step}</Button>
              <Button variant="outline">Send back…</Button>
              <Button variant="ghost">Cancel run</Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function OwnerPage({ ownerId }: { ownerId: string }) {
  const who = teammate(ownerId)
  const owned = linesOwnedBy(ownerId)
  return (
    <OwnerFrame who={who}>
      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Lines</h2>
        {owned.length === 0 && <p className="text-sm text-muted-foreground">Owns no line.</p>}
        <ul className="flex flex-col divide-y rounded-lg border">
          {owned.map((l) => {
            const waiting = runsOf(l.id).filter(needsYou)
            return (
              <li key={l.id} className="px-4 py-2.5 text-sm">
                <To at={`line:${l.id}`} className="font-medium">
                  {lineName(l)}
                </To>
                <span className="ml-2 text-muted-foreground">
                  {runsOf(l.id).filter(unfinished).length} unfinished
                </span>
                {waiting.map((r) => (
                  <p key={r.id} className="mt-1 flex items-center gap-2">
                    <RunBadge run={r} />
                    <To at={`run:${r.id}`}>{r.title}</To>
                  </p>
                ))}
              </li>
            )
          })}
        </ul>
      </section>
    </OwnerFrame>
  )
}

const PICK = "rounded-lg border px-4 py-3 text-left text-sm hover:bg-muted disabled:opacity-50"

/** Set up a line, in a dialog: the factory, then the workbench, then only what is missing. */
function SetupDialog({ open }: { open: boolean }) {
  const [stage, setStage] = useState<"factory" | "workbench" | "answers">("factory")
  const [factoryId, setFactoryId] = useState<string>()
  const [benchId, setBenchId] = useState<string | null>()
  const f = factoryId === undefined ? undefined : factory(factoryId)
  const w = benchId ? workbench(benchId) : undefined
  const gap = f === undefined ? undefined : missing(f, w)
  return (
    <Dialog open={open} onOpenChange={(next) => !next && go("area")}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Set up a line</DialogTitle>
          <DialogDescription>
            {stage === "factory"
              ? "Which factory?"
              : stage === "workbench"
                ? `Where does ${f?.id} run?`
                : `What ${f?.id} still needs${w ? ` on ${w.id}` : ""}`}
          </DialogDescription>
        </DialogHeader>
        {stage === "factory" && (
          <div className="flex flex-col gap-2">
            {FACTORIES.map((option) => (
              <button
                type="button"
                key={option.id}
                className={PICK}
                onClick={() => {
                  setFactoryId(option.id)
                  setStage("workbench")
                }}
              >
                <span className="font-mono font-medium">{option.id}</span>
                <span className="block text-muted-foreground">{option.summary}</span>
              </button>
            ))}
          </div>
        )}
        {stage === "workbench" && f !== undefined && (
          <div className="flex flex-col gap-2">
            {WORKBENCHES.map((option) => {
              const fit = missing(f, option)
              return (
                <button
                  type="button"
                  key={option.id}
                  disabled={fit.blockers.length > 0}
                  className={PICK}
                  onClick={() => {
                    setBenchId(option.id)
                    setStage("answers")
                  }}
                >
                  <span className="font-medium">{option.id}</span>
                  <span className="block text-muted-foreground">
                    {fit.blockers[0] ??
                      `${option.image} · ${option.source}${
                        fit.secrets.length + fit.values.length + fit.commands.length === 0
                          ? " · holds everything"
                          : ""
                      }`}
                  </span>
                </button>
              )
            })}
            <Button
              variant="secondary"
              onClick={() => {
                setBenchId(null)
                setStage("answers")
              }}
            >
              <PlusIcon data-icon="inline-start" />
              New workbench
            </Button>
          </div>
        )}
        {stage === "answers" && gap !== undefined && f !== undefined && (
          <FieldGroup>
            {w === undefined && (
              <>
                <Field>
                  <FieldLabel>Workbench name</FieldLabel>
                  <Input placeholder="my-repo" />
                </Field>
                <Field>
                  <FieldLabel>Workspace</FieldLabel>
                  <Input placeholder="A git URL, or empty for a plain folder" />
                  <FieldDescription>Built from the {f.requires.image} image.</FieldDescription>
                </Field>
              </>
            )}
            {gap.secrets.map((s) => (
              <Field key={s.name}>
                <FieldLabel>{s.label}</FieldLabel>
                <Input type="password" placeholder={s.name} />
              </Field>
            ))}
            {gap.values.map((v) => (
              <Field key={v.name}>
                <FieldLabel>{v.label}</FieldLabel>
                <Input placeholder={v.name} />
              </Field>
            ))}
            {gap.commands.map((c) => (
              <Field key={c}>
                <FieldLabel>{c} commands</FieldLabel>
                <Input placeholder="One per line" />
              </Field>
            ))}
            {gap.secrets.length + gap.values.length + gap.commands.length === 0 && w && (
              <p className="text-sm text-muted-foreground">
                {w.id} holds everything {f.id} requires.
              </p>
            )}
            <Field>
              <FieldLabel>Owner</FieldLabel>
              <FieldDescription>Home, unless you change it later.</FieldDescription>
            </Field>
          </FieldGroup>
        )}
        <DialogFooter>
          {stage !== "factory" && (
            <Button
              variant="ghost"
              onClick={() => setStage(stage === "answers" ? "workbench" : "factory")}
            >
              Back
            </Button>
          )}
          {stage === "answers" && <Button onClick={() => go("area")}>Set up</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
