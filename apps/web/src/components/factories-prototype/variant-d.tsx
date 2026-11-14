// PROTOTYPE (#1299), throwaway. Variant D, "Board and directory", the first iteration on Jorge's
// pick: the Factories screen is B's board of runs over A's directory of factories and their lines,
// both narrowed by one factory filter. The sidebar holds teammates only, with Factories beside
// Library as in A. The line, run, owner and set-up screens are A's.
import { FactoryIcon, PlusIcon } from "lucide-react"
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
import { SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar"
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table"

import { FACTORIES, line, LINES, lineName, needsYou, run, RUNS, TEAMMATES, type Run } from "./data"
import { go, useAt } from "./nav"
import { RunBadge, Shell } from "./shared"
import { LinePage, LineRow, OwnerPage, RunPage, SetupDialog } from "./variant-a"

export function VariantD() {
  const [kind, id] = useAt()
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
            <Badge variant="attention">{RUNS.filter(needsYou).length}</Badge>
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

const COLUMNS: { title: string; holds: (r: Run) => boolean }[] = [
  { title: "Needs you", holds: needsYou },
  { title: "Running", holds: (r) => r.status === "running" },
  {
    title: "Waiting",
    holds: (r) => !needsYou(r) && (r.status === "parked" || r.status === "queued"),
  },
]

function Area({ setup }: { setup: boolean }) {
  const [only, setOnly] = useState<string>()
  const runs = RUNS.filter((r) => only === undefined || line(r.line).factory === only)
  const factories = FACTORIES.filter((f) => only === undefined || f.id === only)
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 md:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-heading text-2xl font-semibold">Factories</h1>
        <Button onClick={() => go("setup")}>
          <PlusIcon data-icon="inline-start" />
          Set up a line
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
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
          </Button>
        ))}
      </div>
      <section className="grid gap-4 md:grid-cols-3">
        {COLUMNS.map((column) => {
          const held = runs.filter(column.holds)
          return (
            <div key={column.title} className="flex flex-col gap-2 rounded-xl bg-muted/50 p-3">
              <h2 className="flex items-center justify-between text-sm font-medium">
                {column.title}
                <span className="text-muted-foreground">{held.length}</span>
              </h2>
              {held.length === 0 && <p className="text-sm text-muted-foreground">None</p>}
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
            </div>
          )
        })}
      </section>
      <section className="flex flex-col gap-4">
        <h2 className="text-sm font-medium text-muted-foreground">
          {only === undefined ? "Factories and their lines" : "Lines"}
        </h2>
        {factories.map((f) => (
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
      </section>
      <SetupDialog open={setup} />
    </div>
  )
}
