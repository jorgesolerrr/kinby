import type { Client, Clock, InstanceSummary, Workbench } from "@kinby/contract"
import { CircleXIcon, PlayIcon, RefreshCwIcon, CircleStopIcon } from "lucide-react"
import type * as React from "react"
import { useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Owner, PageLink, RunBadge } from "@/components/factories-page"
import { PageColumn } from "@/components/page-column"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFollowing } from "@/hooks/use-following"
import { useRead } from "@/hooks/use-read"
import { when } from "@/lib/config-changes"
import { workItem } from "@/lib/factory-runs"
import { shownState, STATES } from "@/lib/instances"
import { dailyBudget, type LineOpened, lineName, readLine, requirements } from "@/lib/lines"
import { type Followed, followOperation } from "@/lib/operation"
import { FACTORIES_PATH, linePath, runPath, tabPath } from "@/lib/selection"

type Caller = Pick<Client, "call">

/**
 * One line's page: its owner, model, daily budget and intake over two tabs. Runs lists every run
 * of the line, each opening its page. Workbench shows the workbench's state with its lifecycle, its image, workspace and
 * repository commands, what the line's factory requires of it, and the other lines on it.
 * `instances` name the owner.
 */
export function LinePage({
  caller,
  clock,
  lineId,
  instances,
}: {
  caller: Caller
  clock: Clock
  lineId: string
  instances: InstanceSummary[]
}) {
  const read = useCallback(() => readLine(caller, lineId), [caller, lineId])
  const { value: opened, failure, readAgain } = useRead(read, clock)
  if (opened === undefined) {
    return (
      <PageColumn className="py-6">
        {failure === undefined ? <Skeleton className="h-72 w-full" /> : <Failure error={failure} />}
      </PageColumn>
    )
  }
  const { line, runs, workbench } = opened
  return (
    <PageColumn className="flex flex-col gap-6 py-6">
      <div className="flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">
          <PageLink href={FACTORIES_PATH} className="hover:underline">
            Factories
          </PageLink>{" "}
          / <span className="font-mono">{line.factory}</span>
        </p>
        <h1 className="font-heading text-2xl font-semibold">{lineName(line, [workbench])}</h1>
      </div>
      <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <Detail term="Owner">
          <PageLink href={tabPath(line.owner_id, "overview")} className="hover:underline">
            <Owner line={line} instances={instances} />
          </PageLink>
        </Detail>
        <Detail term="Model">
          <span className="font-mono text-xs">{line.model ?? "None"}</span>
        </Detail>
        <Detail term="Daily budget">{dailyBudget(line)}</Detail>
        <Detail term="Intake">{line.intake ?? "None"}</Detail>
      </dl>
      <Tabs defaultValue="runs">
        <TabsList variant="line">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="workbench">Workbench</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          {runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Work item</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Step</TableHead>
                  <TableHead>Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map(({ run, needsYou }) => (
                  <TableRow key={run.run_id}>
                    <TableCell>
                      <PageLink href={runPath(run.run_id)} className="font-medium hover:underline">
                        {workItem(run)}
                      </PageLink>
                    </TableCell>
                    <TableCell>
                      <RunBadge run={run} needsYou={needsYou} />
                    </TableCell>
                    <TableCell>{run.step}</TableCell>
                    <TableCell>{when(run.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </TabsContent>
        <TabsContent value="workbench">
          <WorkbenchTab caller={caller} clock={clock} opened={opened} onChanged={readAgain} />
        </TabsContent>
      </Tabs>
    </PageColumn>
  )
}

function WorkbenchTab({
  caller,
  clock,
  opened: { workbench, factory, others, required },
  onChanged,
}: {
  caller: Caller
  clock: Clock
  opened: LineOpened
  onChanged: () => void
}) {
  const commandsId = useId()
  const othersId = useId()
  const asked = requirements(required, workbench)
  return (
    <div className="flex flex-col gap-6 text-sm">
      <dl className="grid grid-cols-2 gap-4">
        <Detail term="State">{STATES[shownState(workbench)]}</Detail>
        <Detail term="Image">
          <span className="font-mono text-xs">{workbench.recipe ?? "default"}</span>
        </Detail>
        <Detail term="Workspace">
          <span className="font-mono text-xs break-all">
            {workbench.workspace_source ?? "A plain folder"}
          </span>
        </Detail>
      </dl>
      <WorkbenchLifecycle
        caller={caller}
        clock={clock}
        workbench={workbench}
        onChanged={onChanged}
      />
      <section aria-labelledby={commandsId} className="flex flex-col gap-2">
        <h2 id={commandsId} className="font-medium">
          Repository commands
        </h2>
        {workbench.repository_commands.length === 0 && (
          <p className="text-muted-foreground">None held.</p>
        )}
        {workbench.repository_commands.map(({ name, commands }) => (
          <div key={name} className="flex flex-col gap-1">
            <p className="text-muted-foreground">{name}</p>
            <pre className="rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
              {commands.join("\n")}
            </pre>
          </div>
        ))}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">What {factory.name} requires</h2>
        {asked.length === 0 ? (
          <p className="text-muted-foreground">Nothing but its image.</p>
        ) : (
          <ul aria-label={`What ${factory.name} requires`} className="flex flex-col gap-1">
            {asked.map(({ label, held }) => (
              <li key={label} className="flex items-center gap-2">
                {label}
                <Badge variant={held ? "outline" : "destructive"}>
                  {held ? "Held" : "Missing"}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby={othersId} className="flex flex-col gap-2">
        <h2 id={othersId} className="font-medium">
          Other lines on {workbench.name}
        </h2>
        {others.length === 0 ? (
          <p className="text-muted-foreground">None</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {others.map((other) => (
              <li key={other.line_id}>
                <PageLink href={linePath(other.line_id)} className="font-mono hover:underline">
                  {other.factory}
                </PageLink>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

const ACTIONS = {
  start: { label: "Start", icon: PlayIcon },
  stop: { label: "Stop", icon: CircleStopIcon },
  recreate: { label: "Recreate", icon: RefreshCwIcon },
}

type Action = keyof typeof ACTIONS

function begin(caller: Caller, action: Action, workbench_id: string) {
  switch (action) {
    case "start":
      return caller.call("workbench.start", { workbench_id })
    case "stop":
      return caller.call("workbench.stop", { workbench_id })
    case "recreate":
      return caller.call("workbench.recreate", { workbench_id })
  }
}

/**
 * Start a workbench meant to be stopped, or stop one meant to run, and recreate either. A
 * finished operation reads the line again through `onChanged`, and a failed one says why.
 */
function WorkbenchLifecycle({
  caller,
  clock,
  workbench,
  onChanged,
}: {
  caller: Caller
  clock: Clock
  workbench: Workbench
  onChanged: () => void
}) {
  // A new object for each click, so asking again follows the operation the hub answers with.
  const [request, setRequest] = useState<{ action: Action }>()
  const follow = useCallback(
    ({ action }: { action: Action }, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => begin(caller, action, workbench.workbench_id),
        (followed) => {
          report(followed)
          if (followed.state === "succeeded") onChanged()
        },
        clock,
      ),
    [caller, clock, workbench.workbench_id, onChanged],
  )
  const followed = useFollowing(request, follow)
  const busy =
    request !== undefined && followed?.state !== "failed" && followed?.state !== "succeeded"
  const offered: Action[] = [workbench.intended_state === "stopped" ? "start" : "stop", "recreate"]
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {offered.map((action) => {
          const { label, icon: Icon } = ACTIONS[action]
          return (
            <Button
              key={action}
              variant={action === "start" ? "default" : "outline"}
              disabled={busy}
              onClick={() => setRequest({ action })}
            >
              {busy && request?.action === action ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Icon data-icon="inline-start" />
              )}
              {label}
            </Button>
          )
        })}
      </div>
      {followed?.state === "failed" && request !== undefined && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>{ACTIONS[request.action].label} failed</AlertTitle>
          <AlertDescription>{followed.detail}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

function Detail({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-muted-foreground">{term}</dt>
      <dd>{children}</dd>
    </div>
  )
}
