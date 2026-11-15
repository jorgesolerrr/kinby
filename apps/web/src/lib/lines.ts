import type {
  Client,
  FactoryRun,
  FactoryRunDetail,
  Line,
  LineSetupDescription,
  StepAttempt,
  StepId,
  WaitingApproval,
  WaitingTakeover,
  Workbench,
} from "@kinby/contract"
import { parse } from "yaml"

import { finished } from "@/lib/factory-runs"
import { money } from "@/lib/stats"

type Caller = Pick<Client, "call">

/** A factory as the Factories area shows it: its steps in order, by id, kind and engine. */
export interface FactorySteps {
  name: string
  /** Only an agent step names an engine. */
  steps: { id: StepId; kind: string; engine?: string }[]
  /** Written the older way: its steps run in instances that hold its roles, and it takes no line. */
  older: boolean
}

/** A run of a line, and whether it waits on the user. */
export interface LineRun {
  run: FactoryRun
  needsYou: boolean
}

/** A line with its runs, newest first as the hub lists them. */
export interface LineRuns {
  line: Line
  runs: LineRun[]
}

/** The hub's factories, its lines with their runs, and the workbenches they run on. */
export interface Floor {
  factories: FactorySteps[]
  lines: LineRuns[]
  workbenches: Workbench[]
}

/** One line's page: the line and its runs, its workbench, and what its factory requires. */
export interface LineOpened extends LineRuns {
  workbench: Workbench
  factory: FactorySteps
  /** The other lines on its workbench. */
  others: Line[]
  /** All the factory requires of a workbench. */
  required: LineSetupDescription
}

/** One thing a factory requires of a workbench, and whether the workbench holds it. */
export interface Requirement {
  label: string
  held: boolean
}

/** One run's page: the run with its attempts, its factory, and the line and workbench it runs on. */
export interface RunOpened {
  detail: FactoryRunDetail
  factory: FactorySteps
  line: Line
  workbench: Workbench
}

/** Read every factory, line and workbench, and each line's runs. */
export async function readFloor(caller: Caller): Promise<Floor> {
  const [{ factories }, { lines }, { workbenches }] = await Promise.all([
    caller.call("factory.list", {}),
    caller.call("line.list", {}),
    caller.call("workbench.list", {}),
  ])
  const read = await Promise.all(factories.map(({ name }) => readFactory(caller, name)))
  const factoryOf = (line: Line) => read.find((factory) => factory.name === line.factory)
  return {
    factories: read,
    lines: await Promise.all(lines.map((line) => readRuns(caller, line, factoryOf(line)))),
    workbenches,
  }
}

/** Read one line with what its page shows. */
export async function readLine(caller: Caller, lineId: string): Promise<LineOpened> {
  const line = await caller.call("line.get", { line_id: lineId })
  const { factory: name, workbench_id, model } = line
  // Described with no workbench: the hub refuses one that has a line of the factory already.
  const [workbench, factory, { lines }, required] = await Promise.all([
    caller.call("workbench.get", { workbench_id }),
    readFactory(caller, name),
    caller.call("line.list", {}),
    caller.call("line.setup.describe", { factory: name, model }),
  ])
  return {
    ...(await readRuns(caller, line, factory)),
    workbench,
    factory,
    others: lines.filter(
      (other) => other.workbench_id === workbench_id && other.line_id !== lineId,
    ),
    required,
  }
}

/**
 * What the factory requires of a workbench, each with whether the workbench holds it, as the hub
 * judges it at setup: a required secret it holds, a required value it holds or that has a default,
 * a list of repository commands, a login signed in, and a browser it was created with.
 */
export function requirements(required: LineSetupDescription, workbench: Workbench): Requirement[] {
  const commands = workbench.repository_commands.map(({ name }) => name)
  const signedIn = (workbench.logins ?? []).filter(({ state }) => state === "signed_in")
  return [
    ...required.secrets
      .filter((field) => field.required)
      .map(({ name, label }) => ({ label, held: workbench.secrets.includes(name) })),
    ...required.values
      .filter((field) => field.required)
      .map((field) => ({
        label: field.label,
        held: field.default != null || field.name in workbench.values,
      })),
    ...required.commands.map((name) => ({
      label: `${name} commands`,
      held: commands.includes(name),
    })),
    ...required.logins.map(({ id, label }) => ({
      label,
      held: signedIn.some((login) => login.id === id),
    })),
    ...(required.browser ? [{ label: "Browser", held: workbench.browser === true }] : []),
  ]
}

/** Read one run of a line with what its page shows. */
export async function readRun(caller: Caller, runId: string): Promise<RunOpened> {
  const detail = await caller.call("factory.run.get", { run_id: runId })
  const { line_id, factory: name } = detail.run
  if (line_id == null) throw new Error(`The run ${runId} belongs to no line.`)
  const line = await caller.call("line.get", { line_id })
  const [workbench, factory] = await Promise.all([
    caller.call("workbench.get", { workbench_id: line.workbench_id }),
    readFactory(caller, name),
  ])
  return { detail, factory, line, workbench }
}

/** A line as the user knows it, "software on kinby". */
export function lineName(line: Line, workbenches: Workbench[]): string {
  const workbench = workbenches.find((held) => held.workbench_id === line.workbench_id)
  return `${line.factory} on ${workbench?.name ?? line.workbench_id}`
}

/** A line's daily budget, or None. */
export function dailyBudget(line: Line): string {
  return line.usd_per_day === null ? "None" : `${money(line.usd_per_day)} a day`
}

/** How many runs of the lines wait on the user. */
export function needingYou(lines: LineRuns[]): number {
  return lines.flatMap(({ runs }) => runs).filter(({ needsYou }) => needsYou).length
}

async function readFactory(caller: Caller, name: string): Promise<FactorySteps> {
  const { files } = await caller.call("factory.get", { name })
  // The hub reads it with PyYAML, which resolves `<<` merge keys.
  const file = parse(files["factory.yaml"] ?? "", { merge: true }) as {
    instances?: unknown
    steps?: { id: StepId; kind: string; engine?: string }[]
  }
  return {
    name,
    steps: (file.steps ?? []).map(({ id, kind, engine }) => ({ id, kind, engine })),
    older: file.instances !== undefined,
  }
}

/** What a run waits on the user for. */
export type Wait =
  | { kind: "needs-human" }
  | { kind: "approval" }
  /** An ask from the turn of an agent step: an approval of a call, or a takeover. */
  | { kind: "ask"; ask: WaitingApproval | WaitingTakeover }

/**
 * What the run waits on the user for, if anything: it stopped for a human, waits at an approve
 * step, or its turn waits on an ask or a takeover the user has not answered. `stepKind` is the kind
 * of the step it is at, and `last` its last attempt, which only a run parked on a turn needs.
 */
export function waitingOn(
  run: FactoryRun,
  stepKind: string | undefined,
  last: StepAttempt | undefined,
): Wait | undefined {
  if (run.status === "needs-human") return { kind: "needs-human" }
  if (run.status !== "parked") return undefined
  if (stepKind === "approve") return { kind: "approval" }
  const ask = stepKind === "wait" || last?.answer != null ? undefined : last?.ask
  return ask == null ? undefined : { kind: "ask", ask }
}

/** The kind of the step the run is at, by the factory's file. */
export function stepKind(factory: FactorySteps | undefined, run: FactoryRun): string | undefined {
  return factory?.steps.find((step) => step.id === run.step)?.kind
}

/** The line's runs, each with whether it waits on the user, as `waitingOn` says. */
async function readRuns(
  caller: Caller,
  line: Line,
  factory: FactorySteps | undefined,
): Promise<LineRuns> {
  const { runs } = await caller.call("factory.run.list", { line_id: line.line_id, limit: null })
  const lastAttempt = async (run: FactoryRun) => {
    const { attempts } = await caller.call("factory.run.get", { run_id: run.run_id })
    return attempts.at(-1)
  }
  return {
    line,
    runs: await Promise.all(
      runs.map(async (run) => {
        const kind = stepKind(factory, run)
        const onTurn = run.status === "parked" && kind !== "approve" && kind !== "wait"
        const last = onTurn ? await lastAttempt(run) : undefined
        return { run, needsYou: waitingOn(run, kind, last) !== undefined }
      }),
    ),
  }
}

/** The runs that have not finished. */
export function unfinished(runs: LineRun[]): LineRun[] {
  return runs.filter(({ run }) => !finished(run))
}
