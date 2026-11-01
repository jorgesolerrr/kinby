// PROTOTYPE: static mock data for /proto/shell. Throwaway.
import type { Avatar } from "@kinby/contract"

export type Status =
  | "idle"
  | "running"
  | "awaiting_approval"
  | "awaiting_takeover"
  | "awaiting_reply"
  | "failed"

export interface MockThread {
  id: string
  title: string
  status: Status
  day: "Today" | "Yesterday" | "Earlier"
  routine?: boolean
}

export interface MockInstance {
  id: string
  name: string
  avatar: Avatar
  home?: boolean
  state: "running" | "stopped"
  role?: string
  views: { id: string; title: string }[]
  threads: MockThread[]
}

export const INSTANCES: MockInstance[] = [
  {
    id: "kin",
    name: "Kin",
    avatar: { shape: "circle", color: "green" },
    home: true,
    state: "running",
    views: [
      { id: "inbox", title: "Inbox digest" },
      { id: "trips", title: "Trips" },
    ],
    threads: [
      { id: "k1", title: "Plan the Lisbon trip in March", status: "awaiting_reply", day: "Today" },
      { id: "k2", title: "Renew the car insurance", status: "awaiting_takeover", day: "Today" },
      { id: "k3", title: "Morning brief", status: "idle", day: "Today", routine: true },
      { id: "k4", title: "Draft a reply to the landlord", status: "running", day: "Today" },
      { id: "k5", title: "Compare the two dentist quotes", status: "idle", day: "Yesterday" },
      {
        id: "k6",
        title: "Weekly finance check",
        status: "failed",
        day: "Yesterday",
        routine: true,
      },
      { id: "k7", title: "Gift ideas for Ana", status: "idle", day: "Earlier" },
      { id: "k8", title: "Book the boiler service", status: "idle", day: "Earlier" },
    ],
  },
  {
    id: "scout",
    name: "Scout",
    avatar: { shape: "squircle", color: "violet" },
    state: "running",
    views: [{ id: "reading", title: "Reading list" }],
    threads: [
      {
        id: "s1",
        title: "What changed in the EU AI Act this month",
        status: "running",
        day: "Today",
      },
      { id: "s2", title: "Summarise the vector DB benchmarks", status: "idle", day: "Yesterday" },
      { id: "s3", title: "Papers on agent memory", status: "idle", day: "Earlier" },
    ],
  },
  {
    id: "coder",
    name: "Coder",
    avatar: { shape: "square", color: "blue" },
    role: "coder in software",
    state: "running",
    views: [],
    threads: [
      {
        id: "c1",
        title: "#981 Thread, composer and message stream",
        status: "awaiting_approval",
        day: "Today",
      },
      { id: "c2", title: "#962 Fix the stats range header", status: "idle", day: "Yesterday" },
      { id: "c3", title: "#958 Hub secret picker", status: "idle", day: "Earlier" },
    ],
  },
  {
    id: "ledger",
    name: "Ledger",
    avatar: { shape: "circle", color: "amber" },
    state: "stopped",
    views: [],
    threads: [{ id: "l1", title: "Q3 expenses", status: "idle", day: "Earlier" }],
  },
]

export const REMOVED = [
  { id: "old", name: "Tester", avatar: { shape: "circle", color: "gray" } as Avatar },
]

export const needsYou = (t: MockThread) =>
  t.status === "awaiting_approval" ||
  t.status === "awaiting_takeover" ||
  t.status === "awaiting_reply"

export const waiting = (i: MockInstance) => i.threads.filter(needsYou).length

export const instanceById = (id: string) => INSTANCES.find((i) => i.id === id) ?? INSTANCES[0]

/** What the main area shows. Each variant reaches these screens its own way. */
export type Screen =
  | { kind: "home" }
  | { kind: "thread"; instanceId: string; threadId: string }
  | { kind: "threads"; instanceId: string }
  | { kind: "instance"; instanceId: string; tab: InstanceTab }
  | { kind: "view"; instanceId: string; viewId: string }
  | { kind: "library" }
  | { kind: "hub"; page: HubPage }
  | { kind: "new-instance" }

export type InstanceTab = "overview" | "config" | "routines" | "memory" | "stats"
export type HubPage = "general" | "usage" | "secrets" | "channels" | "instances"

export const INSTANCE_TABS: { id: InstanceTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "config", label: "Config" },
  { id: "routines", label: "Routines" },
  { id: "memory", label: "Memory" },
  { id: "stats", label: "Stats" },
]

export const HUB_PAGES: { id: HubPage; label: string }[] = [
  { id: "general", label: "General" },
  { id: "usage", label: "Usage" },
  { id: "secrets", label: "Hub secrets" },
  { id: "channels", label: "Channels" },
  { id: "instances", label: "Instances" },
]

export function describe(screen: Screen): string {
  switch (screen.kind) {
    case "home":
      return "home"
    case "thread":
      return `thread ${screen.instanceId}/${screen.threadId}`
    case "threads":
      return `all threads ${screen.instanceId}`
    case "instance":
      return `instance ${screen.instanceId} · ${screen.tab}`
    case "view":
      return `view ${screen.instanceId}/${screen.viewId}`
    case "library":
      return "library"
    case "hub":
      return `hub · ${screen.page}`
    case "new-instance":
      return "new instance"
  }
}
