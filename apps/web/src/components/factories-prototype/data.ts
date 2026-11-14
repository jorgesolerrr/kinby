// PROTOTYPE (#1299), throwaway. Fixed data for the Factories area: the playground hub after its
// five factory instances became workbenches and lines (ADR 0125). Nothing here reaches a hub.

export type Color = "blue" | "violet" | "green" | "amber" | "red" | "gray"

export interface Teammate {
  id: string
  name: string
  home: boolean
  color: Color
  shape: "circle" | "squircle" | "square"
  threads: string[]
}

export const TEAMMATES: Teammate[] = [
  {
    id: "home",
    name: "Home",
    home: true,
    color: "violet",
    shape: "circle",
    threads: ["Plan the week", "Why did #1305 fail checks?", "CEC module 3 outline"],
  },
  {
    id: "finance",
    name: "Finance",
    home: false,
    color: "green",
    shape: "squircle",
    threads: ["October expenses", "Tax form 303"],
  },
]

export type StepKind = "code" | "command" | "agent" | "wait" | "approval"

export interface Step {
  id: string
  kind: StepKind
  /** For an agent step: "kinby" or "claude". For a wait: the signal. */
  detail?: string
}

/** What a factory requires of a workbench. */
export interface Requires {
  image: string
  secrets: { name: string; label: string }[]
  values: { name: string; label: string }[]
  commands: string[]
  logins: string[]
  browser: boolean
}

export interface Factory {
  id: string
  summary: string
  steps: Step[]
  requires: Requires
  intake: string
}

export const FACTORIES: Factory[] = [
  {
    id: "software",
    summary: "Turns issues labeled ready-for-agent into pull requests, then answers their reviews.",
    steps: [
      { id: "ticket", kind: "code" },
      { id: "setup", kind: "command" },
      { id: "implement", kind: "agent", detail: "claude" },
      { id: "answer", kind: "agent", detail: "claude" },
      { id: "fix", kind: "agent", detail: "claude" },
      { id: "checks", kind: "command" },
      { id: "publish", kind: "code" },
      { id: "babysit", kind: "wait", detail: "pull_request_review" },
    ],
    requires: {
      image: "coder",
      secrets: [
        { name: "GH_TOKEN", label: "GitHub token" },
        { name: "CLAUDE_CODE_OAUTH_TOKEN", label: "Claude Code token" },
        { name: "GIT_USER_NAME", label: "Commit author name" },
        { name: "GIT_USER_EMAIL", label: "Commit author email" },
      ],
      values: [],
      commands: ["setup", "checks"],
      logins: [],
      browser: false,
    },
    intake: "Hourly, and on each GitHub delivery",
  },
  {
    id: "course",
    summary: "Builds a Moodle course module from an outline, with your approval between drafts.",
    steps: [
      { id: "outline", kind: "agent", detail: "kinby" },
      { id: "approve-outline", kind: "approval" },
      { id: "draft", kind: "agent", detail: "kinby" },
      { id: "approve-draft", kind: "approval" },
      { id: "publish", kind: "code" },
    ],
    requires: {
      image: "browser",
      secrets: [{ name: "MOODLE_TOKEN", label: "Moodle web service token" }],
      values: [
        { name: "MOODLE_URL", label: "Moodle address" },
        { name: "COURSE_CATEGORY", label: "Course category" },
      ],
      commands: [],
      logins: [],
      browser: true,
    },
    intake: "When you hand it a module in chat",
  },
  {
    id: "reading-brief",
    summary: "Reads the week's saved articles and writes a one-page brief.",
    steps: [
      { id: "collect", kind: "code" },
      { id: "brief", kind: "agent", detail: "kinby" },
      { id: "deliver", kind: "code" },
    ],
    requires: {
      image: "base",
      secrets: [{ name: "READWISE_TOKEN", label: "Readwise token" }],
      values: [],
      commands: [],
      logins: [],
      browser: false,
    },
    intake: "Fridays at 07:00",
  },
]

export interface Workbench {
  id: string
  image: string
  /** A git clone's remote, or a plain folder. */
  source: string
  secrets: string[]
  values: Record<string, string>
  commands: Record<string, string[]>
  logins: string[]
  browser: boolean
  picks: string[]
  state: "running" | "stopped"
}

export const WORKBENCHES: Workbench[] = [
  {
    id: "kinby",
    image: "coder",
    source: "github.com/jorgesolerrr/kinby",
    secrets: ["GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "GIT_USER_NAME", "GIT_USER_EMAIL"],
    values: {},
    commands: {
      setup: ["uv sync", "bun install"],
      checks: ["ruff check", "ty check", "pytest -n auto", "bun run check"],
    },
    logins: ["claude"],
    browser: false,
    picks: ["tdd", "codebase-design", "unslop", "pr"],
    state: "running",
  },
  {
    id: "cec-backend",
    image: "coder",
    source: "github.com/pb-cec/cec-backend",
    secrets: ["GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "GIT_USER_NAME", "GIT_USER_EMAIL"],
    values: {},
    commands: { setup: ["pnpm install"], checks: ["pnpm lint", "pnpm test"] },
    logins: ["claude"],
    browser: false,
    picks: ["tdd", "pr"],
    state: "running",
  },
  {
    id: "landing",
    image: "coder",
    source: "github.com/jorgesolerrr/landing",
    secrets: ["GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "GIT_USER_NAME", "GIT_USER_EMAIL"],
    values: {},
    commands: { setup: ["bun install"], checks: ["bun run build"] },
    logins: ["claude"],
    browser: false,
    picks: ["pr"],
    state: "stopped",
  },
  {
    id: "cec-moodle",
    image: "browser",
    source: "folder",
    secrets: ["MOODLE_TOKEN"],
    values: { MOODLE_URL: "https://aulavirtual.cec.edu.ec", COURSE_CATEGORY: "Diplomados" },
    commands: {},
    logins: [],
    browser: true,
    picks: ["unslop"],
    state: "running",
  },
  {
    id: "reading",
    image: "base",
    source: "folder",
    secrets: ["READWISE_TOKEN"],
    values: {},
    commands: {},
    logins: [],
    browser: false,
    picks: [],
    state: "running",
  },
]

export interface Line {
  id: string
  factory: string
  workbench: string
  owner: string
  model: string
  budget: number
  spentToday: number
}

export const LINES: Line[] = [
  {
    id: "software@kinby",
    factory: "software",
    workbench: "kinby",
    owner: "home",
    model: "claude-opus-5-5",
    budget: 40,
    spentToday: 23.4,
  },
  {
    id: "software@cec-backend",
    factory: "software",
    workbench: "cec-backend",
    owner: "home",
    model: "claude-opus-5-5",
    budget: 25,
    spentToday: 6.1,
  },
  {
    id: "software@landing",
    factory: "software",
    workbench: "landing",
    owner: "home",
    model: "claude-sonnet-5-5",
    budget: 10,
    spentToday: 0,
  },
  {
    id: "course@cec-moodle",
    factory: "course",
    workbench: "cec-moodle",
    owner: "home",
    model: "claude-opus-5-5",
    budget: 15,
    spentToday: 4.8,
  },
  {
    id: "reading-brief@reading",
    factory: "reading-brief",
    workbench: "reading",
    owner: "finance",
    model: "claude-haiku-5-5",
    budget: 2,
    spentToday: 0,
  },
]

export type RunStatus = "running" | "queued" | "parked" | "needs-human" | "done" | "cancelled"

/** Why a run waits on the user, when it does. */
export type Waiting =
  | { kind: "approval"; summary: string }
  | { kind: "takeover"; reason: string }
  | { kind: "needs-human"; reason: string }

export type Outcome =
  | "running"
  | "clean"
  | "failed"
  | "timed out"
  | "asked"
  | "needs human"
  | "waiting"
  | "approved"
  | "sent back"

export type Entry =
  | { kind: "say"; text: string }
  | { kind: "tool"; name: string; text: string }
  | { kind: "user"; text: string }
  | { kind: "cmd"; text: string; exit: number; tail?: string }
  | { kind: "call"; text: string; values?: Record<string, string> }
  | { kind: "event"; text: string }

export interface Attempt {
  step: string
  n: number
  /** Who made the attempt: a kinby turn, a Claude Code session, kinby running commands or code, a wait. */
  runner: "kinby turn" | "Claude Code" | "commands" | "code" | "wait" | "you"
  session?: string
  started: string
  took: string
  outcome: Outcome
  summary: string
  cost?: number
  transcript: Entry[]
}

export interface Run {
  id: string
  line: string
  title: string
  item: string
  status: RunStatus
  step: string | null
  started: string
  cost: number
  waiting?: Waiting
  attempts: Attempt[]
}

const IMPLEMENT_1305: Entry[] = [
  { kind: "user", text: "prompts/implement.md, issue #1305, branch agent/1305-line-owner-moves" },
  { kind: "say", text: "Reading the ticket and the line module before I change anything." },
  { kind: "tool", name: "Read", text: "src/kinby/hub/lines.py" },
  { kind: "tool", name: "Grep", text: "change_owner, 4 matches" },
  { kind: "say", text: "Owner change moves runs and intake but not marked memory. Adding a move." },
  { kind: "tool", name: "Edit", text: "src/kinby/hub/lines.py (+42 −6)" },
  { kind: "tool", name: "Bash", text: "pytest tests/hub/test_lines.py -q → 18 passed" },
  { kind: "say", text: "Committed: The owner change moves the memory marked with its workbench." },
]

const FIX_1305: Entry[] = [
  {
    kind: "user",
    text: "prompts/fix.md with results.failure: ty check, 2 errors in memory/move.py",
  },
  { kind: "tool", name: "Read", text: "src/kinby/memory/move.py" },
  { kind: "tool", name: "Edit", text: "src/kinby/memory/move.py (+3 −3)" },
  { kind: "say", text: "Narrowed the mark type. ty check passes locally." },
]

export const RUNS: Run[] = [
  {
    id: "r-1305",
    line: "software@kinby",
    title: "Changing a line's owner moves its marked memory",
    item: "issue 1305",
    status: "needs-human",
    step: "checks",
    started: "Today 09:12",
    cost: 11.82,
    waiting: {
      kind: "needs-human",
      reason: "checks failed after 2 send-backs to fix: pytest, test_owner_change_keeps_copy",
    },
    attempts: [
      {
        step: "ticket",
        n: 1,
        runner: "code",
        started: "09:12",
        took: "2s",
        outcome: "clean",
        summary: "Trusted author wrote every comment.",
        transcript: [{ kind: "call", text: "vet_ticket(issue=1305)", values: { trusted: "true" } }],
      },
      {
        step: "setup",
        n: 1,
        runner: "commands",
        started: "09:12",
        took: "1m 40s",
        outcome: "clean",
        summary: "2 commands ran.",
        transcript: [
          { kind: "cmd", text: "uv sync", exit: 0 },
          { kind: "cmd", text: "bun install", exit: 0 },
        ],
      },
      {
        step: "implement",
        n: 1,
        runner: "Claude Code",
        session: "sess_8f2a",
        started: "09:14",
        took: "22m",
        outcome: "clean",
        summary: "One commit, 4 files. Title and body written.",
        cost: 7.9,
        transcript: IMPLEMENT_1305,
      },
      {
        step: "answer",
        n: 1,
        runner: "Claude Code",
        session: "sess_8f2a",
        started: "09:36",
        took: "40s",
        outcome: "clean",
        summary: "No review threads to answer yet.",
        cost: 0.2,
        transcript: [{ kind: "say", text: "No pull request yet, so no threads. Nothing changed." }],
      },
      {
        step: "fix",
        n: 1,
        runner: "Claude Code",
        session: "sess_8f2a",
        started: "09:37",
        took: "30s",
        outcome: "clean",
        summary: "Nothing failed, nothing changed.",
        cost: 0.1,
        transcript: [{ kind: "say", text: "results.failure is empty. Nothing to repair." }],
      },
      {
        step: "checks",
        n: 1,
        runner: "commands",
        started: "09:38",
        took: "6m",
        outcome: "failed",
        summary: "ty check failed. Sent back to fix (1 of 2).",
        transcript: [
          { kind: "cmd", text: "uv sync", exit: 0 },
          { kind: "cmd", text: "ruff check", exit: 0 },
          {
            kind: "cmd",
            text: "ty check",
            exit: 1,
            tail: "error[invalid-argument-type]: memory/move.py:41 expected Mark, got str",
          },
        ],
      },
      {
        step: "fix",
        n: 2,
        runner: "Claude Code",
        session: "sess_8f2a",
        started: "09:44",
        took: "4m",
        outcome: "clean",
        summary: "Narrowed the mark type.",
        cost: 1.4,
        transcript: FIX_1305,
      },
      {
        step: "checks",
        n: 2,
        runner: "commands",
        started: "09:48",
        took: "7m",
        outcome: "failed",
        summary: "pytest failed. Sent back to fix (2 of 2).",
        transcript: [
          { kind: "cmd", text: "ty check", exit: 0 },
          {
            kind: "cmd",
            text: "pytest -n auto",
            exit: 1,
            tail: "FAILED tests/hub/test_lines.py::test_owner_change_keeps_copy",
          },
        ],
      },
      {
        step: "fix",
        n: 3,
        runner: "Claude Code",
        session: "sess_8f2a",
        started: "09:55",
        took: "6m",
        outcome: "clean",
        summary: "Kept a copy for an old owner with another line on the workbench.",
        cost: 2.1,
        transcript: [
          { kind: "tool", name: "Edit", text: "src/kinby/hub/lines.py (+9 −1)" },
          { kind: "say", text: "The old owner keeps a copy when it owns another line there." },
        ],
      },
      {
        step: "checks",
        n: 3,
        runner: "commands",
        started: "10:01",
        took: "7m",
        outcome: "needs human",
        summary: "pytest failed again. No send-backs left.",
        transcript: [
          {
            kind: "cmd",
            text: "pytest -n auto",
            exit: 1,
            tail: "FAILED tests/hub/test_lines.py::test_owner_change_keeps_copy\nAssertionError: 3 facts != 4",
          },
          { kind: "event", text: "Labeled #1305 ready-for-human." },
        ],
      },
    ],
  },
  {
    id: "r-1312",
    line: "software@kinby",
    title: "kinby hub lists lines by workbench",
    item: "issue 1312",
    status: "running",
    step: "implement",
    started: "Today 10:20",
    cost: 3.1,
    attempts: [
      {
        step: "ticket",
        n: 1,
        runner: "code",
        started: "10:20",
        took: "2s",
        outcome: "clean",
        summary: "Trusted.",
        transcript: [{ kind: "call", text: "vet_ticket(issue=1312)" }],
      },
      {
        step: "setup",
        n: 1,
        runner: "commands",
        started: "10:20",
        took: "1m",
        outcome: "clean",
        summary: "2 commands ran.",
        transcript: [
          { kind: "cmd", text: "uv sync", exit: 0 },
          { kind: "cmd", text: "bun install", exit: 0 },
        ],
      },
      {
        step: "implement",
        n: 1,
        runner: "Claude Code",
        session: "sess_c41d",
        started: "10:21",
        took: "12m so far",
        outcome: "running",
        summary: "Writing the change.",
        cost: 3.1,
        transcript: [
          { kind: "tool", name: "Read", text: "src/kinby/cli/hub.py" },
          { kind: "say", text: "Adding --workbench to `kinby hub lines`." },
        ],
      },
    ],
  },
  {
    id: "r-1290",
    line: "software@kinby",
    title: "Workbench record shows its recreate reason",
    item: "issue 1290",
    status: "parked",
    step: "babysit",
    started: "Yesterday",
    cost: 9.4,
    attempts: [
      {
        step: "publish",
        n: 1,
        runner: "code",
        started: "Yesterday 16:02",
        took: "8s",
        outcome: "clean",
        summary: "Opened PR #1301.",
        transcript: [{ kind: "call", text: "publish_pull_request()", values: { pr: "1301" } }],
      },
      {
        step: "babysit",
        n: 1,
        runner: "wait",
        started: "Yesterday 16:02",
        took: "18h so far",
        outcome: "waiting",
        summary: "Waiting for a review on PR #1301.",
        transcript: [{ kind: "event", text: "Parked until pull_request_review on #1301." }],
      },
    ],
  },
  {
    id: "r-1288",
    line: "software@kinby",
    title: "Instance list holds teammates only",
    item: "issue 1288",
    status: "done",
    step: null,
    started: "Nov 12",
    cost: 14.2,
    attempts: [],
  },
  {
    id: "r-88",
    line: "software@cec-backend",
    title: "Certificates endpoint pages its results",
    item: "issue 88",
    status: "running",
    step: "checks",
    started: "Today 10:02",
    cost: 5.3,
    attempts: [],
  },
  {
    id: "r-85",
    line: "software@cec-backend",
    title: "Enrollment import skips blank rows",
    item: "issue 85",
    status: "done",
    step: null,
    started: "Nov 11",
    cost: 6.6,
    attempts: [],
  },
  {
    id: "r-12",
    line: "software@landing",
    title: "Pricing section in Spanish",
    item: "issue 12",
    status: "done",
    step: null,
    started: "Nov 3",
    cost: 2.0,
    attempts: [],
  },
  {
    id: "r-m3",
    line: "course@cec-moodle",
    title: "Diplomado IA, module 3: Agents",
    item: "module 3",
    status: "parked",
    step: "approve-outline",
    started: "Today 08:30",
    cost: 2.4,
    waiting: {
      kind: "approval",
      summary: "Outline for module 3, 6 lessons, 2 graded quizzes. Draft in outline.md.",
    },
    attempts: [
      {
        step: "outline",
        n: 1,
        runner: "kinby turn",
        started: "08:30",
        took: "9m",
        outcome: "clean",
        summary: "6 lessons, 2 quizzes, in outline.md.",
        cost: 2.4,
        transcript: [
          { kind: "user", text: "Outline module 3 of Diplomado IA: Agents." },
          {
            kind: "tool",
            name: "browser.open",
            text: "aulavirtual.cec.edu.ec/course/view.php?id=212",
          },
          { kind: "say", text: "Modules 1 and 2 use 5 lessons each; I'll keep the pace." },
          { kind: "tool", name: "write", text: "outline.md (6 lessons, 2 quizzes)" },
          { kind: "say", text: "Outline ready for your approval." },
        ],
      },
      {
        step: "approve-outline",
        n: 1,
        runner: "you",
        started: "08:39",
        took: "1h 50m so far",
        outcome: "waiting",
        summary: "Waiting for your approval.",
        transcript: [{ kind: "event", text: "Notified you: Outline for module 3 is ready." }],
      },
    ],
  },
  {
    id: "r-m4",
    line: "course@cec-moodle",
    title: "Diplomado IA, module 4: Evaluation",
    item: "module 4",
    status: "parked",
    step: "draft",
    started: "Yesterday",
    cost: 5.0,
    waiting: { kind: "takeover", reason: "Moodle asks for a second factor to sign in." },
    attempts: [
      {
        step: "outline",
        n: 1,
        runner: "kinby turn",
        started: "Yesterday 11:00",
        took: "8m",
        outcome: "clean",
        summary: "5 lessons.",
        cost: 2.0,
        transcript: [{ kind: "tool", name: "write", text: "outline.md" }],
      },
      {
        step: "approve-outline",
        n: 1,
        runner: "you",
        started: "Yesterday 11:08",
        took: "2h",
        outcome: "approved",
        summary: "Approved with notes: shorter quizzes.",
        transcript: [{ kind: "user", text: "Approved. Notes: keep quizzes to 5 questions." }],
      },
      {
        step: "draft",
        n: 1,
        runner: "kinby turn",
        started: "Yesterday 13:10",
        took: "14m",
        outcome: "asked",
        summary: "Asked for a takeover: Moodle sign-in needs a second factor.",
        cost: 3.0,
        transcript: [
          { kind: "tool", name: "browser.open", text: "aulavirtual.cec.edu.ec/login" },
          { kind: "say", text: "Moodle wants a code from your phone. I need you to sign in." },
          { kind: "event", text: "Asked for a takeover." },
        ],
      },
    ],
  },
  {
    id: "r-w47",
    line: "reading-brief@reading",
    title: "Week 47 brief",
    item: "week 47",
    status: "queued",
    step: null,
    started: "Fri 07:00",
    cost: 0,
    attempts: [],
  },
  {
    id: "r-w46",
    line: "reading-brief@reading",
    title: "Week 46 brief",
    item: "week 46",
    status: "done",
    step: null,
    started: "Nov 8",
    cost: 0.3,
    attempts: [],
  },
]

export const STATUS_LABEL: Record<RunStatus, string> = {
  running: "Running",
  queued: "Queued",
  parked: "Parked",
  "needs-human": "Needs a human",
  done: "Done",
  cancelled: "Cancelled",
}

export function standing(run: Run): string {
  return run.step === null ? STATUS_LABEL[run.status] : `${STATUS_LABEL[run.status]} at ${run.step}`
}

export function runnerKind(attempt: Attempt): StepKind {
  switch (attempt.runner) {
    case "kinby turn":
    case "Claude Code":
      return "agent"
    case "commands":
      return "command"
    case "code":
      return "code"
    case "wait":
      return "wait"
    case "you":
      return "approval"
  }
}

export const money = (n: number) => `$${n.toFixed(2)}`

export const factory = (id: string) => FACTORIES.find((f) => f.id === id)!
export const workbench = (id: string) => WORKBENCHES.find((w) => w.id === id)!
export const line = (id: string) => LINES.find((l) => l.id === id)!
export const teammate = (id: string) => TEAMMATES.find((t) => t.id === id)!
export const run = (id: string) => RUNS.find((r) => r.id === id)!
export const runsOf = (lineId: string) => RUNS.filter((r) => r.line === lineId)
export const unfinished = (r: Run) => r.status !== "done" && r.status !== "cancelled"
export const needsYou = (r: Run) => r.waiting !== undefined
export const linesOwnedBy = (ownerId: string) => LINES.filter((l) => l.owner === ownerId)
export const lineName = (l: Line) => `${l.factory} on ${l.workbench}`

/** What the factory requires that the workbench does not hold yet; `blockers` cannot be answered. */
export function missing(f: Factory, w: Workbench | undefined) {
  const r = f.requires
  if (w === undefined) {
    return {
      secrets: r.secrets,
      values: r.values,
      commands: r.commands,
      blockers: [] as string[],
      held: [] as string[],
    }
  }
  const blockers: string[] = []
  if (w.image !== r.image)
    blockers.push(`Built from the ${w.image} image; ${f.id} needs ${r.image}.`)
  if (r.browser && !w.browser) blockers.push("Has no browser; create a workbench with one.")
  for (const login of r.logins) {
    if (!w.logins.includes(login)) blockers.push(`Not signed in to ${login}. Sign in first.`)
  }
  const held = [
    ...r.secrets.filter((s) => w.secrets.includes(s.name)).map((s) => s.label),
    ...r.values.filter((v) => v.name in w.values).map((v) => v.label),
    ...r.commands.filter((c) => c in w.commands).map((c) => `${c} commands`),
  ]
  return {
    secrets: r.secrets.filter((s) => !w.secrets.includes(s.name)),
    values: r.values.filter((v) => !(v.name in w.values)),
    commands: r.commands.filter((c) => !(c in w.commands)),
    blockers,
    held,
  }
}
