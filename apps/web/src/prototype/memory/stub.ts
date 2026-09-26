// PROTOTYPE, throwaway. A stub instance's memory: the profile and knowledge graph nodes.
export const INSTANCE = { id: "coder", name: "Coder" }

export type Kind = "fact" | "episode"
export type Source = "agent" | "user"

export type MemoryNode = {
  node: string
  kind: Kind
  date: string
  description: string
  subjects: string[]
  body: string
  source: Source
  thread?: { id: string; title: string }
  turn?: string
  tools?: string[]
  tombstone: boolean
}

const THREADS = {
  flaky: { id: "0193a1f2", title: "Fix flaky hub test" },
  review: { id: "0193a0c4", title: "Review the usage PR" },
  setup: { id: "01939e11", title: "Set up the coder" },
}

export const PROFILE = `# Jorge

- Writes Python with uv and TypeScript with Bun. Prefers lean code over machinery.
- Timezone: Europe/Madrid. Don't notify between 23:00 and 08:00.
- Replies in English in PRs and issues, Spanish in chat is fine.
- Standing instruction: never merge a PR; label it merge-ready and tell me.
`

const node = (n: Omit<MemoryNode, "tombstone" | "source"> & { source?: Source }): MemoryNode => ({
  source: "agent",
  tombstone: false,
  ...n,
})

export const NODES: MemoryNode[] = [
  node({
    node: "2026-09-25-0193a1f2-flaky-hub-test-was-a-port-race",
    kind: "episode",
    date: "2026-09-25",
    description: "Fixed the flaky hub test: two tests raced for the same port",
    subjects: ["hub", "tests", "flaky"],
    body: "What happened: `test_drain_then_stop` failed one run in ten.\n\nWhat was decided: bind to port 0 and read the chosen port back.\n\nWhat should have gone differently: I opened eight files before running the test once. Run the failing test first.",
    thread: THREADS.flaky,
    turn: "0193a1f2-7c01",
    tools: ["bash", "read_file", "edit_file", "bash"],
  }),
  node({
    node: "2026-09-25-0193a1f5-prefers-port-zero-in-tests",
    kind: "fact",
    date: "2026-09-25",
    description: "Jorge wants tests to bind port 0, never a fixed port",
    subjects: ["tests", "jorge"],
    body: "Said while fixing the flaky hub test.",
    thread: THREADS.flaky,
  }),
  node({
    node: "2026-09-24-0193a0c4-usage-pr-review",
    kind: "episode",
    date: "2026-09-24",
    description: "Reviewed the usage PR and asked for a split by usage source",
    subjects: ["usage", "review"],
    body: "What happened: the stats view mixed API and subscription counts.\n\nWhat was decided: split by usage source before merge.",
    thread: THREADS.review,
    turn: "0193a0c4-11aa",
    tools: ["gh", "read_file"],
  }),
  node({
    node: "2026-09-20-01939f02-merge-strategy",
    kind: "fact",
    date: "2026-09-20",
    description: "Jorge prefers squash merges",
    subjects: ["git", "jorge"],
    body: "He squashes every PR on kinby.",
    thread: THREADS.review,
  }),
  node({
    node: "2026-09-12-01939e11-merge-strategy",
    kind: "fact",
    date: "2026-09-12",
    description: "Jorge prefers rebase merges",
    subjects: ["git", "jorge"],
    body: "Older fact. The newer one about squash merges wins.",
    thread: THREADS.setup,
  }),
  node({
    node: "2026-09-12-01939e11-coder-setup",
    kind: "episode",
    date: "2026-09-12",
    description: "Set up the coder: Codex login and the GitHub token",
    subjects: ["setup", "codex", "github"],
    body: "What happened: the Codex login needed the device flow.\n\nWhat should have gone differently: ask for the token scope before trying.",
    thread: THREADS.setup,
    turn: "01939e11-0f3b",
    tools: ["bash", "codex_login"],
  }),
  node({
    node: "2026-09-10-01939d77-ci-runs-on-linux",
    kind: "fact",
    date: "2026-09-10",
    description: "The kinby gate runs on Linux; pytest can't import the locks on Windows",
    subjects: ["ci", "windows", "tests"],
    body: "Run `bun run check` inside WSL on Windows.",
    source: "user",
  }),
]

export const AGENT_WRITES: MemoryNode[] = [
  node({
    node: "2026-09-26-0193a2aa-oxfmt-before-commit",
    kind: "fact",
    date: "2026-09-26",
    description: "Run oxfmt before committing web changes",
    subjects: ["web", "lint"],
    body: "Learned when a commit failed the gate.",
    thread: THREADS.flaky,
  }),
]
