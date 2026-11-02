// PROTOTYPE: static mock library for /proto/library. Instances come from the shell's mock. Throwaway.
export type Kind = "skill" | "mcp_server" | "factory"
export type Source = "shipped" | "written" | "git"
export type PickState = "up-to-date" | "behind" | "edited"

export interface Use {
  instanceId: string
  state: PickState
}

export interface Entry {
  kind: Kind
  name: string
  description: string
  source: Source
  edited?: boolean
  newer?: boolean
  git?: { url: string; path: string; commit: string }
  usedBy: Use[]
}

export interface Run {
  id: string
  title: string
  status: "running" | "queued" | "parked" | "needs-human" | "done"
  step: string
  when: string
}

export interface Factory extends Entry {
  kind: "factory"
  roles: { name: string; image: string; picks: string[]; fields: string[] }[]
  steps: string[]
  files: string[]
  runs: Run[]
}

export const KINDS: { kind: Kind; title: string; noun: string; none: string }[] = [
  { kind: "skill", title: "Skills", noun: "skill", none: "No skills yet" },
  { kind: "mcp_server", title: "MCP servers", noun: "MCP server", none: "No MCP servers yet" },
  { kind: "factory", title: "Factories", noun: "factory", none: "No factories yet" },
]

export const SOURCES: Record<Source, string> = {
  shipped: "Shipped",
  written: "Written",
  git: "Git",
}

export const PICK_STATES: Record<PickState, string> = {
  "up-to-date": "Up to date",
  behind: "Behind the library",
  edited: "Edited in the instance",
}

const skill = (
  name: string,
  description: string,
  source: Source,
  usedBy: Use[],
  more: Partial<Entry> = {},
): Entry => ({ kind: "skill", name, description, source, usedBy, ...more })

export const SKILLS: Entry[] = [
  skill(
    "trip-planner",
    "Plan a trip end to end: dates, flights, places to stay, a day-by-day outline. Use when the user asks to plan or book travel.",
    "written",
    [{ instanceId: "kin", state: "up-to-date" }],
  ),
  skill(
    "paper-digest",
    "Read a paper and write a one-page digest: claim, method, evidence, what it changes. Use when the user shares a paper or asks what one says.",
    "written",
    [{ instanceId: "scout", state: "behind" }],
  ),
  skill(
    "grill-with-docs",
    "Grilling that records settled terms in a glossary and decisions as ADRs. Use when the user asks to be grilled inside a repo.",
    "git",
    [],
    {
      newer: true,
      git: { url: "github.com/mattpocock/skills", path: "grill-with-docs", commit: "4f2a91c" },
    },
  ),
  skill(
    "research",
    "Investigate a question against high-trust primary sources and capture the findings as a Markdown file.",
    "git",
    [
      { instanceId: "scout", state: "up-to-date" },
      { instanceId: "kin", state: "up-to-date" },
    ],
    { git: { url: "github.com/jorgesolerrr/skills", path: "research", commit: "a81d3e0" } },
  ),
  skill(
    "unslop",
    'Cut AI tells from prose a human will read. Use when drafting docs, emails or posts, or when the user says "sounds like AI".',
    "shipped",
    [
      { instanceId: "kin", state: "edited" },
      { instanceId: "coder", state: "up-to-date" },
    ],
    { edited: true },
  ),
  skill(
    "hub-report",
    'Answer "how is the hub going?" from the instances\' statuses, usage, factory runs and the threads that need the user.',
    "shipped",
    [{ instanceId: "kin", state: "up-to-date" }],
  ),
  skill(
    "create-instance",
    "Create an instance through onboarding, as a vanilla instance or a factory role, with its model, prompt, picks and secrets.",
    "shipped",
    [{ instanceId: "kin", state: "up-to-date" }],
  ),
  skill(
    "write-routine",
    "Draft kinby routine files for recurring or manual work, in this instance or another one the hub runs.",
    "shipped",
    [{ instanceId: "kin", state: "behind" }],
  ),
  skill(
    "adversarial-review",
    "Two-axis code review (Standards, Spec) of a diff, run by the other model's CLI so the reviewer never shares the implementer's context.",
    "shipped",
    [{ instanceId: "coder", state: "up-to-date" }],
  ),
  skill(
    "tdd",
    "Test-driven development. Use when the user wants to build features or fix bugs test-first.",
    "shipped",
    [{ instanceId: "coder", state: "up-to-date" }],
  ),
  skill(
    "codebase-design",
    "Deep-module vocabulary: module, interface, depth, seam, adapter.",
    "shipped",
    [{ instanceId: "coder", state: "up-to-date" }],
  ),
  skill("pr", "Use when writing a PR body.", "shipped", [
    { instanceId: "coder", state: "up-to-date" },
  ]),
  skill(
    "write-factory",
    "Write or edit a factory with factory_write, its steps, hooks, instance templates and prompts, so it passes the factory check.",
    "shipped",
    [],
  ),
]

export const MCP_SERVERS: Entry[] = [
  {
    kind: "mcp_server",
    name: "github",
    description: "Issues, pull requests and code search on GitHub · URL",
    source: "written",
    usedBy: [
      { instanceId: "coder", state: "up-to-date" },
      { instanceId: "kin", state: "up-to-date" },
    ],
  },
  {
    kind: "mcp_server",
    name: "notion",
    description: "Pages and databases in the user's Notion · uvx notion-mcp",
    source: "written",
    usedBy: [{ instanceId: "kin", state: "behind" }],
  },
  {
    kind: "mcp_server",
    name: "google-calendar",
    description: "Read and write events on Google Calendar · URL",
    source: "written",
    usedBy: [{ instanceId: "kin", state: "up-to-date" }],
  },
  {
    kind: "mcp_server",
    name: "arxiv",
    description: "Search and fetch papers from arXiv · uvx arxiv-mcp-server",
    source: "written",
    usedBy: [{ instanceId: "scout", state: "up-to-date" }],
  },
  {
    kind: "mcp_server",
    name: "firefly",
    description: "Accounts and transactions in Firefly III · URL",
    source: "written",
    usedBy: [],
  },
]

export const FACTORIES: Factory[] = [
  {
    kind: "factory",
    name: "software",
    description:
      "Turns GitHub issues labeled ready-for-agent into agent pull requests, and answers their reviews until they are merge-ready.",
    source: "shipped",
    edited: true,
    usedBy: [{ instanceId: "coder", state: "up-to-date" }],
    roles: [
      {
        name: "coder",
        image: "coder",
        picks: ["adversarial-review", "codebase-design", "pr", "tdd", "unslop"],
        fields: ["Repository", "Model", "GitHub token", "Webhook secret", "Claude Code token"],
      },
    ],
    steps: [
      "prepare",
      "setup",
      "implement",
      "answer",
      "fix",
      "checks",
      "publish",
      "babysit",
      "assess",
    ],
    files: [
      "prompts/implement.md",
      "prompts/review.md",
      "prompts/fix.md",
      "prompts/answer.md",
      "instances/coder/SYSTEM.md",
      "instances/coder/kinby.toml",
      "instances/coder/skills/implement-ticket/SKILL.md",
      "instances/coder/skills/repository-survey/SKILL.md",
      "instances/coder/routines/scan/ROUTINE.md",
    ],
    runs: [
      {
        id: "r1",
        title: "#1003 Library page",
        status: "needs-human",
        step: "checks",
        when: "12 min ago",
      },
      {
        id: "r2",
        title: "#1105 Instance page: Stats as one scroll",
        status: "running",
        step: "babysit",
        when: "40 min ago",
      },
      {
        id: "r3",
        title: "#1110 Overview as rows",
        status: "done",
        step: "assess",
        when: "yesterday",
      },
      {
        id: "r4",
        title: "#1112 Routines as rows",
        status: "done",
        step: "assess",
        when: "yesterday",
      },
    ],
  },
]

export const ALL: Entry[] = [...SKILLS, ...MCP_SERVERS, ...FACTORIES]

export const SKILL_MD = `---
name: trip-planner
description: Plan a trip end to end: dates, flights, places to stay, a day-by-day outline.
---

# Trip planner

1. Pin the dates and who travels. Ask once if either is missing.
2. Search flights with the browser; keep the three best by price and duration.
3. Shortlist places to stay near what the user wants to see.
4. Write a day-by-day outline to \`trips/<city>.md\` and publish it to the Trips view.
5. Book nothing without an approval.
`

export const FACTORY_YAML = `# The software factory: it turns GitHub issues labeled ready-for-agent into agent
# pull requests, and answers their reviews until they are merge-ready.
name: software
instances:
  coder:
    image: coder
    suggested_picks: [adversarial-review, codebase-design, pr, tdd, unslop]
steps:
  - id: prepare
  - id: setup
  - id: implement
  ...`

export const GIT_PREVIEW = {
  url: "github.com/mattpocock/skills",
  commit: "9c04b17",
  skills: [
    { name: "grilling", description: "Relentless interview on a plan, decision, or idea." },
    {
      name: "domain-modeling",
      description: "Build and sharpen a project's domain model.",
    },
    { name: "prototype", description: "Build a throwaway prototype to answer a design question." },
    { name: "grill-with-docs", description: "Already in the library", present: true },
  ],
}

export const byKind = (kind: Kind) => ALL.filter((e) => e.kind === kind)

export const matches = (e: Entry, query: string) =>
  `${e.name} ${e.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
