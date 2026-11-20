import {
  CallError,
  type FactoryRun,
  type FactoryRunDetail,
  type Line,
  type StepAttempt,
  type Workbench,
} from "@kinby/contract"
import { type Answers, instanceSummary, stubCaller } from "@kinby/contract/testing"

export const instances = [
  instanceSummary({ instance_id: "home", manifest_id: "home", persona_name: "Kinby", home: true }),
  instanceSummary({ instance_id: "finance", manifest_id: "finance", persona_name: "Finance" }),
]

const FILES: Record<string, string> = {
  software: `
name: software
requires: { image: coder }
steps:
  - { id: ticket, kind: code, call: vet_ticket }
  - { id: implement, kind: agent, engine: claude, prompt: Implement it. }
  - { id: checks, kind: command, run: ["bun run check"] }
  - { id: publish, kind: code, call: publish }
`,
  course: `
name: course
requires: { image: browser, browser: true }
steps:
  - { id: draft, kind: agent, engine: kinby, prompt: Draft it. }
  - { id: settle, kind: wait, until: 1h }
  - { id: review, kind: approve, summary: Read the draft. }
`,
  "reading-brief": `
instances: { reader: { image: default } }
steps:
  - { id: read, kind: agent, prompt: Read it. }
`,
}

export function workbench(fields: Pick<Workbench, "workbench_id" | "name"> & Partial<Workbench>) {
  return {
    hub_secrets: {},
    intended_state: "running" as const,
    process: "running" as const,
    recipe: "coder",
    recreate_reasons: [],
    repository_commands: [],
    secrets: [],
    values: {},
    workspace_source: null,
    ...fields,
  }
}

export const workbenches: Workbench[] = [
  workbench({
    workbench_id: "wb-kinby",
    name: "kinby",
    workspace_source: "https://github.com/jorgesolerrr/kinby",
    secrets: ["GH_TOKEN"],
    values: { REPOSITORY: "jorgesolerrr/kinby" },
    logins: [{ id: "claude", label: "Claude Code", description: "", state: "signed_in" }],
    repository_commands: [
      { name: "setup", commands: ["uv sync", "bun install"] },
      { name: "checks", commands: ["bun run check"] },
    ],
  }),
  workbench({ workbench_id: "wb-landing", name: "landing" }),
  workbench({ workbench_id: "wb-moodle", name: "cec-moodle", recipe: "browser" }),
]

function line(fields: Pick<Line, "line_id" | "factory" | "workbench_id"> & Partial<Line>) {
  return { owner_id: "home", model: null, usd_per_day: null, intake: null, ...fields }
}

const lines: Line[] = [
  line({
    line_id: "l-kinby",
    factory: "software",
    workbench_id: "wb-kinby",
    model: "claude-opus-5-5",
    usd_per_day: 20,
    intake: "software-kinby-intake",
  }),
  line({
    line_id: "l-landing",
    factory: "software",
    workbench_id: "wb-landing",
    owner_id: "finance",
  }),
  line({ line_id: "l-course", factory: "course", workbench_id: "wb-moodle" }),
  line({ line_id: "l-notes", factory: "course", workbench_id: "wb-kinby" }),
]

function run(
  fields: Pick<FactoryRun, "run_id" | "line_id" | "status"> & Partial<FactoryRun>,
): FactoryRun {
  return {
    factory: "software",
    instance_id: "home",
    step: null,
    work_item: { issue: 12 },
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:00:00Z",
    ...fields,
  }
}

const runs: FactoryRun[] = [
  run({
    run_id: "r-stopped",
    line_id: "l-kinby",
    status: "needs-human",
    step: "implement",
    work_item: { issue: 1305 },
  }),
  run({
    run_id: "r-running",
    line_id: "l-kinby",
    status: "running",
    step: "implement",
    work_item: { issue: 1310 },
  }),
  run({ run_id: "r-done", line_id: "l-kinby", status: "done", work_item: { issue: 1290 } }),
  run({
    run_id: "r-asking",
    line_id: "l-landing",
    status: "parked",
    step: "implement",
    work_item: { issue: 40 },
  }),
  run({
    run_id: "r-queued",
    line_id: "l-landing",
    status: "queued",
    step: "ticket",
    work_item: { issue: 41 },
  }),
  run({
    run_id: "r-approve",
    line_id: "l-course",
    factory: "course",
    status: "parked",
    step: "review",
    work_item: { unit: 3 },
  }),
  run({
    run_id: "r-settling",
    line_id: "l-course",
    factory: "course",
    status: "parked",
    step: "settle",
    work_item: { unit: 4 },
  }),
]

export function attempt(fields: Partial<StepAttempt> = {}): StepAttempt {
  return {
    attempt: 1,
    ended_at: null,
    ending: null,
    outcome: null,
    started_at: "2026-10-12T09:10:00Z",
    step: "implement",
    summary: "",
    values: {},
    ...fields,
  }
}

/** The stopped run's attempts: implement, sent back once by its failing checks, then out of tries. */
const stoppedAttempts: StepAttempt[] = [
  attempt({
    step: "ticket",
    started_at: "2026-10-12T09:00:00Z",
    ended_at: "2026-10-12T09:00:04Z",
    ending: "clean",
    summary: "The ticket is the maintainer's.",
  }),
  attempt({
    started_at: "2026-10-12T09:01:00Z",
    ended_at: "2026-10-12T09:21:00Z",
    ending: "clean",
    session: "claude-1",
    summary: "Implemented the board.",
    usage: { api: { input_tokens: 1_000, output_tokens: 200, cost: 0.42 } },
  }),
  attempt({
    step: "checks",
    started_at: "2026-10-12T09:21:00Z",
    ended_at: "2026-10-12T09:24:00Z",
    ending: "failed",
    summary: '"bun run check" exited with code 1.\nFAIL src/board.test.tsx',
  }),
  attempt({
    attempt: 2,
    started_at: "2026-10-12T09:24:00Z",
    ended_at: "2026-10-12T09:40:00Z",
    ending: "failed",
    session: "claude-1",
    summary: "The board test still fails.",
  }),
]

/** What Claude Code streamed in the stopped run's first attempt at implement. */
const implementing = [
  { type: "system", subtype: "init", session_id: "claude-1" },
  {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "Reading the ticket." },
        { type: "tool_use", id: "t1", name: "Bash", input: { command: "gh issue view 1305" } },
      ],
    },
  },
]

/** Each run's attempts, where the fixture holds more than one waiting at its step. */
function attempts(run: FactoryRun): StepAttempt[] {
  if (run.run_id === "r-stopped") return stoppedAttempts
  return [attempt({ step: run.step ?? "implement", ask: run.run_id === "r-asking" ? ask : null })]
}

const ask = {
  turn_id: "turn-1",
  request: { type: "takeover.requested" as const, takeover_id: "t-1", reason: "Sign in." },
}

const field = (
  name: string,
  label: string,
  more: { kind?: "secret" | "config"; required?: boolean; default?: string } = {},
) => ({
  name,
  label,
  description: "",
  kind: "secret" as const,
  type: "text" as const,
  required: true,
  ...more,
})

const login = (id: string, label: string) => ({
  id,
  label,
  description: "",
  command: [],
  prompt_pattern: "",
  volume: id,
})

/** How the hub answers with the fixture's factories, lines, workbenches and runs. */
export const answers: Answers = {
  "factory.list": () => ({
    factories: Object.keys(FILES).map((name) => ({ name, source: "hub" as const, version: null })),
  }),
  "factory.get": ({ name }) => ({
    name,
    source: "hub",
    hash: "h",
    files: { "factory.yaml": FILES[name] ?? "" },
    version: null,
    declared_in: {},
    differs_from_shipped: false,
    shipped_update_waiting: false,
    differing_steps: [],
  }),
  "line.list": () => ({ lines }),
  "line.get": ({ line_id }) => {
    const found = lines.find((held) => held.line_id === line_id)
    if (found === undefined) throw new Error(`No line ${line_id}`)
    return found
  },
  "workbench.list": () => ({ workbenches }),
  "workbench.get": ({ workbench_id }) => {
    const found = workbenches.find((held) => held.workbench_id === workbench_id)
    if (found === undefined) throw new Error(`No workbench ${workbench_id}`)
    return found
  },
  "factory.run.list": ({ line_id }) => ({
    runs: runs.filter((held) => held.line_id === line_id),
    cursor: null,
  }),
  "factory.run.get": ({ run_id }) => detail(run_id),
  "factory.run.transcript": ({ run_id, step, attempt }) => ({
    events: run_id === "r-stopped" && step === "implement" && attempt === 1 ? implementing : [],
    cursor: null,
  }),
  // As the hub does, it refuses to describe a new line where the factory has one already.
  "line.setup.describe": ({ workbench_id }) => {
    if (workbench_id != null) {
      throw new CallError({
        code: "INVALID_SETUP",
        message: "The factory is set up on the workbench already.",
        retryable: false,
      })
    }
    return {
      browser: true,
      commands: ["setup", "checks", "lint"],
      logins: [login("claude", "Claude Code"), login("codex", "Codex")],
      secrets: [
        field("GH_TOKEN", "GitHub token"),
        field("NPM_TOKEN", "npm token"),
        field("SENTRY_DSN", "Sentry address", { required: false }),
      ],
      values: [
        field("REPOSITORY", "Repository", { kind: "config" }),
        field("BRANCH", "Default branch", { kind: "config", default: "main" }),
        field("LABEL", "Issue label", { kind: "config" }),
      ],
      workbench: [],
    }
  },
}

/** The run of the fixture by its ID, with its attempts and the steps it can go back to. */
export function detail(runId: string): FactoryRunDetail {
  const run = runs.find((held) => held.run_id === runId)
  if (run === undefined) throw new Error(`No run ${runId}`)
  const steps = FILES[run.factory]?.match(/id: \w+/g)?.map((id) => id.slice(4)) ?? []
  const earlier = run.step === null ? [] : steps.slice(0, steps.indexOf(run.step))
  return { run, attempts: attempts(run), earlier_steps: earlier }
}

/** A hub that answers as `answers` says, and as `more` says over it. */
export function hub(more: Answers = {}) {
  return stubCaller({ ...answers, ...more })
}
