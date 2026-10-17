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
    needsYou: 1,
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
