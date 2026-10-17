// PROTOTYPE fixtures: believable data for the component and layout variants. No hub, no client.

export type AvatarColor = "blue" | "violet" | "green" | "amber" | "red" | "gray"
export type AvatarShape = "circle" | "squircle" | "square"

export interface ProtoInstance {
  id: string
  name: string
  avatar: { color: AvatarColor; shape: AvatarShape }
  state: "running" | "stopped" | "starting" | "setup"
  needsYou: number
  role: string
  home?: boolean
}

export const instances: ProtoInstance[] = [
  {
    id: "atlas",
    name: "Atlas",
    avatar: { color: "green", shape: "circle" },
    state: "running",
    needsYou: 2,
    role: "Personal assistant",
    home: true,
  },
  {
    id: "nora",
    name: "Nora",
    avatar: { color: "violet", shape: "squircle" },
    state: "running",
    needsYou: 0,
    role: "Research",
  },
  {
    id: "forge",
    name: "Forge",
    avatar: { color: "amber", shape: "square" },
    state: "setup",
    needsYou: 0,
    role: "Software factory coder",
  },
  {
    id: "ledger",
    name: "Ledger",
    avatar: { color: "blue", shape: "circle" },
    state: "stopped",
    needsYou: 0,
    role: "Finances",
  },
]

export type Starter =
  | { kind: "user" }
  | { kind: "routine"; name: string }
  | { kind: "factory-run"; factory: string; step: string }

export type Step =
  | { kind: "text"; text: string }
  | {
      kind: "tool"
      name: string
      target: string
      status: "auto" | "approved" | "waiting" | "denied" | "running"
      ms?: number
    }

export interface ProtoTurn {
  id: string
  startedBy: Starter
  at: string
  request: string
  steps: Step[]
  end?: { kind: "done"; tokens: number } | { kind: "failed"; message: string }
  recap?: "episode" | "none"
}

export const turns: ProtoTurn[] = [
  {
    id: "t1",
    startedBy: { kind: "routine", name: "Morning brief" },
    at: "Today, 8:00",
    request: "Read my calendar and inbox, then write a plan for the day.",
    steps: [
      { kind: "tool", name: "calendar.list", target: "today", status: "auto", ms: 412 },
      {
        kind: "tool",
        name: "gmail.search",
        target: "is:unread newer_than:1d",
        status: "auto",
        ms: 980,
      },
      {
        kind: "text",
        text: "Three meetings today. The dentist at 16:30 clashes with the design review, which Ana moved yesterday. Two emails need you: the passport renewal form and Marta's invoice.",
      },
    ],
    end: { kind: "done", tokens: 8412 },
    recap: "episode",
  },
  {
    id: "t2",
    startedBy: { kind: "user" },
    at: "Today, 9:14",
    request: "Move the dentist to Thursday and tell Ana.",
    steps: [
      {
        kind: "tool",
        name: "calendar.update",
        target: "Dentist · Thu 16:30",
        status: "approved",
        ms: 680,
      },
      { kind: "text", text: "I moved the dentist to Thursday at 16:30. Next I'll email Ana." },
      { kind: "tool", name: "gmail.send", target: "ana@example.com", status: "waiting" },
    ],
  },
]

export const approval = {
  call: "gmail.send ana@example.com",
  rule: "ask: outbound email",
  arguments: {
    to: "ana@example.com",
    subject: "Dentist moved to Thursday",
    body: "Hi Ana, I moved my dentist appointment to Thursday, so the design review can stay on Wednesday at 16:30.",
  },
}

export const tiles = [
  { title: "Turns", value: "142", note: "118 by you · 24 by routines" },
  { title: "Spend", value: "$4.82", note: "$3.10 API · $1.72 plan" },
  { title: "Approvals", value: "17", note: "15 approved · 2 denied" },
]

export const threads = [
  { id: "th1", title: "Dentist and Ana", status: "awaiting_approval" as const, when: "9:14" },
  { id: "th2", title: "Morning brief", status: "done" as const, when: "8:00", routine: true },
  { id: "th3", title: "Passport renewal", status: "done" as const, when: "Yesterday" },
  { id: "th4", title: "Invoice for Marta", status: "failed" as const, when: "Mon" },
]

// Round two: the agents sidebar with its threads beside it, and the hub's Library and Secrets.

export type ThreadState = "idle" | "running" | "awaiting_approval" | "failed"

export interface ProtoThread {
  id: string
  title: string
  status: ThreadState
  when: string
  day: "Today" | "Yesterday" | "This week" | "Earlier"
  preview: string
  routine?: string
}

export const threadsByAgent: Record<string, ProtoThread[]> = {
  atlas: [
    {
      id: "a1",
      title: "Dentist and Ana",
      status: "awaiting_approval",
      when: "9:14",
      day: "Today",
      preview: "Wants to send ana@example.com the new time",
    },
    {
      id: "a2",
      title: "Morning brief",
      status: "idle",
      when: "8:00",
      day: "Today",
      preview: "Three meetings today. The dentist clashes with…",
      routine: "Morning brief",
    },
    {
      id: "a3",
      title: "Inbox sweep",
      status: "running",
      when: "now",
      day: "Today",
      preview: "Reading 14 unread emails",
      routine: "Inbox sweep",
    },
    {
      id: "a4",
      title: "Passport renewal",
      status: "idle",
      when: "18:40",
      day: "Yesterday",
      preview: "The form is filled. You sign it at the office.",
    },
    {
      id: "a5",
      title: "Invoice for Marta",
      status: "failed",
      when: "Mon",
      day: "This week",
      preview: "gmail.send failed: the token expired",
    },
    {
      id: "a6",
      title: "Weekly review",
      status: "idle",
      when: "Fri",
      day: "This week",
      preview: "Six things done, two carried over",
      routine: "Weekly review",
    },
    {
      id: "a7",
      title: "Flights to Lisbon",
      status: "idle",
      when: "Sep 28",
      day: "Earlier",
      preview: "TAP at 7:05 is the cheapest with a bag",
    },
    {
      id: "a8",
      title: "Gym schedule",
      status: "idle",
      when: "Sep 22",
      day: "Earlier",
      preview: "Tuesdays and Thursdays at 7:30",
    },
    {
      id: "a9",
      title: "Birthday gift for Leo",
      status: "idle",
      when: "Sep 19",
      day: "Earlier",
      preview: "Ordered the Lego set, arrives Friday",
    },
  ],
  nora: [
    {
      id: "n1",
      title: "Vector databases compared",
      status: "running",
      when: "now",
      day: "Today",
      preview: "Reading the pgvector benchmarks",
    },
    {
      id: "n2",
      title: "Paper digest",
      status: "idle",
      when: "7:30",
      day: "Today",
      preview: "Four papers worth reading this week",
      routine: "Paper digest",
    },
    {
      id: "n3",
      title: "Competitors of kinby",
      status: "idle",
      when: "Tue",
      day: "This week",
      preview: "Eleven projects, three self-hosted",
    },
  ],
  forge: [],
  ledger: [
    {
      id: "l1",
      title: "September expenses",
      status: "idle",
      when: "Oct 2",
      day: "Earlier",
      preview: "€2,140 spent, 8% under budget",
    },
  ],
}

export const agentPages = ["Overview", "Memory", "Stats", "Settings"] as const

export interface ProtoLibraryEntry {
  kind: "skill" | "factory"
  name: string
  source: "shipped" | "written" | "git"
  description: string
  git?: { url: string; commit: string }
  edited?: boolean
  newer?: boolean
  files: string[]
}

export const libraryEntries: ProtoLibraryEntry[] = [
  {
    kind: "skill",
    name: "morning-brief",
    source: "shipped",
    description: "Read the calendar and the inbox, then write a plan for the day.",
    files: [],
  },
  {
    kind: "skill",
    name: "inbox-sweep",
    source: "shipped",
    description: "Sort unread mail into reply, read later and archive.",
    edited: true,
    files: ["rules.md"],
  },
  {
    kind: "skill",
    name: "weekly-review",
    source: "shipped",
    description: "Sum up the week: what got done, what carries over.",
    newer: true,
    files: [],
  },
  {
    kind: "skill",
    name: "travel-planner",
    source: "written",
    description: "Find flights and hotels for a trip and hold the best pick.",
    files: ["airlines.md"],
  },
  {
    kind: "skill",
    name: "expense-report",
    source: "written",
    description: "Turn receipts in the inbox into a monthly expense table.",
    files: [],
  },
  {
    kind: "skill",
    name: "tdd",
    source: "git",
    description: "Test-driven development: red, green, refactor.",
    git: { url: "github.com/jorgesolerrr/skills", commit: "a41c9e2" },
    files: ["tests.md", "mocking.md"],
  },
  {
    kind: "skill",
    name: "unslop",
    source: "git",
    description: "Cut AI tells from prose a human will read.",
    git: { url: "github.com/jorgesolerrr/skills", commit: "a41c9e2" },
    newer: true,
    files: [],
  },
  {
    kind: "skill",
    name: "research",
    source: "git",
    description: "Investigate a question against primary sources and write it up.",
    git: { url: "github.com/jorgesolerrr/skills", commit: "a41c9e2" },
    files: [],
  },
  {
    kind: "factory",
    name: "software-factory",
    source: "shipped",
    description: "Tickets in, pull requests out: a coder and a reviewer.",
    files: ["roles/coder.md", "roles/reviewer.md"],
  },
  {
    kind: "factory",
    name: "content-studio",
    source: "written",
    description: "Draft, edit and schedule posts from a brief.",
    files: ["roles/writer.md"],
  },
]

export const skillBody = `---
name: morning-brief
description: Read the calendar and the inbox, then write a plan for the day.
---

# Morning brief

1. List today's events with calendar.list.
2. Search the inbox for unread mail from the last day.
3. Flag clashes between events, and mail that asks for an action.
4. Write the plan: meetings first, then the two or three things only the user can do.

Keep it under 150 words. Lead with what changed since yesterday.
`

export interface ProtoSecret {
  name: string
  links: { agent: string; field: string; running: boolean }[]
}

export const secrets: ProtoSecret[] = [
  {
    name: "ANTHROPIC_API_KEY",
    links: [
      { agent: "Atlas", field: "model.api_key", running: true },
      { agent: "Nora", field: "model.api_key", running: true },
      { agent: "Forge", field: "model.api_key", running: false },
    ],
  },
  {
    name: "GITHUB_TOKEN",
    links: [{ agent: "Forge", field: "tools.github.token", running: false }],
  },
  {
    name: "GMAIL_OAUTH",
    links: [{ agent: "Atlas", field: "tools.gmail.credentials", running: true }],
  },
  {
    name: "GOOGLE_CALENDAR",
    links: [{ agent: "Atlas", field: "tools.calendar.credentials", running: true }],
  },
  {
    name: "BRAVE_SEARCH_KEY",
    links: [
      { agent: "Nora", field: "tools.search.api_key", running: true },
      { agent: "Atlas", field: "tools.search.api_key", running: true },
    ],
  },
  {
    name: "BANK_EXPORT_TOKEN",
    links: [{ agent: "Ledger", field: "tools.bank.token", running: false }],
  },
  { name: "OPENAI_API_KEY", links: [] },
]
