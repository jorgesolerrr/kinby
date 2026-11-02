// PROTOTYPE: static mock data for /proto/instance, shaped after the shipped instance page's
// contract types. Kin is the home instance; the bar switches its lifecycle state. Throwaway.

/** The lifecycle states the bar switches between. */
export const LIVES = ["running", "stopped", "update", "failed"] as const
export type Life = (typeof LIVES)[number]

export const CORE = { running: "0.42.1", hub: "0.43.0" }
export const UPTIME = "3 days"

export interface Login {
  id: string
  label: string
  description: string
  state: "pending" | "signed_in" | "failed"
}

export const LOGINS: Login[] = [
  {
    id: "claude",
    label: "Claude",
    description: "Plan runs through the Claude subscription",
    state: "signed_in",
  },
  {
    id: "chatgpt",
    label: "ChatGPT",
    description: "Plan runs through the ChatGPT subscription",
    state: "pending",
  },
]

export const PROFILES = [
  { name: "Personal Google", site: "https://accounts.google.com" },
  { name: "Iberia", site: "https://www.iberia.com" },
  { name: "Mapfre", site: "https://www.mapfre.es" },
]

export const VAULT = [
  { name: "Iberia Plus", site: "https://www.iberia.com" },
  { name: "Mapfre", site: "https://www.mapfre.es" },
  { name: "Endesa", site: "https://www.endesa.com" },
  { name: "Idealista", site: "https://www.idealista.com" },
]

export const TOOLSET = {
  builtin: ["Browser", "Shell", "Files", "Web search", "Memory", "Views"],
  mcp: [
    { name: "gmail", tools: 9, state: "ready" as const },
    { name: "google-calendar", tools: 6, state: "ready" as const },
    { name: "notion", tools: 14, state: "failed" as const },
  ],
}

export interface ConfigSection {
  id: string
  label: string
  hint: string
  group: "Behavior" | "Capabilities" | "Instance"
}

export const CONFIG_SECTIONS: ConfigSection[] = [
  {
    id: "behavior",
    label: "Behavior prompt",
    hint: "SYSTEM.md, how the agent acts",
    group: "Behavior",
  },
  {
    id: "recap",
    label: "Recap prompt",
    hint: "RECAP.md, what a recap looks at",
    group: "Behavior",
  },
  {
    id: "permissions",
    label: "Permissions",
    hint: "ceiling, mode, tool rules, shell patterns",
    group: "Behavior",
  },
  { id: "manifest", label: "Manifest", hint: "models, budgets, timezone", group: "Behavior" },
  {
    id: "skills",
    label: "Skills",
    hint: "library picks, instance, kinby, workspace",
    group: "Capabilities",
  },
  { id: "tools", label: "Tools", hint: "what the instance can do", group: "Capabilities" },
  {
    id: "secrets",
    label: "Secrets and login",
    hint: "write-only values, sign-in",
    group: "Instance",
  },
  { id: "core", label: "Core version", hint: "the kinby it runs, update", group: "Instance" },
]

export const BEHAVIOR_PROMPT = `# Kin

You are Kin, Jorge's personal teammate. You run his errands: travel, bills, the house, the car,
the calendar. You live on his hub and remember what he tells you.

## How you work

- Ask before you pay, book or send anything in his name.
- Prefer his saved sign-ins over asking him to log in.
- When a task takes more than a few minutes, say what you're doing and keep going.
- Spanish with Spanish companies, English with him unless he writes in Spanish.

## What you never do

- Share his documents or numbers with anyone he didn't name.
- Accept terms or cookie banners beyond the essentials.
`

export const MANIFEST = {
  models: { main: "anthropic:claude-opus-5-5", recap: "anthropic:claude-haiku-5-5", embed: "" },
  budgets: { steps: "60", tokens: "400000", seconds: "", dollars: "5" },
  timezone: "Europe/Madrid",
  defaultTools: true,
  shellTimeout: "120",
  recap: "every" as "every" | "off",
  askRating: true,
  persona: "Kin",
}

export const PERMISSIONS = {
  ceiling: "auto",
  mode: "ask",
  rules: [
    { tool: "browser.navigate", rule: "allow" },
    { tool: "browser.submit", rule: "ask" },
    { tool: "gmail.send", rule: "ask" },
    { tool: "shell", rule: "follow" },
    { tool: "files.delete", rule: "deny" },
  ],
  denied: ["rm -rf *", "curl * | sh"],
  ask: ["git push *", "npm publish *"],
}

export const SECRETS = [
  { label: "Anthropic API key", variable: "ANTHROPIC_API_KEY", is_set: true, hub: "anthropic" },
  { label: "OpenAI API key", variable: "OPENAI_API_KEY", is_set: false, optional: true },
  { label: "Notion token", variable: "NOTION_TOKEN", is_set: true },
]

export const SKILLS = [
  { name: "trip-planner", source: "library" },
  { name: "bill-tracker", source: "library" },
  { name: "house-admin", source: "instance" },
  { name: "remember", source: "kinby" },
]

export interface Routine {
  id: string
  name: string
  description: string
  trigger: string
  human: string
  enabled: boolean
  next?: string
  last?: string
  outcome?: "work" | "no-work" | "failed" | "parked"
  failures?: number
  mode: "read-only" | "ask" | "auto"
}

export const ROUTINES: Routine[] = [
  {
    id: "brief",
    name: "morning-brief",
    description: "Calendar, weather and the inbox's top three, before 8",
    trigger: "Schedule 30 7 * * 1-5",
    human: "Weekdays at 07:30",
    enabled: true,
    next: "Tomorrow 07:30",
    last: "Today 07:30",
    outcome: "work",
    mode: "read-only",
  },
  {
    id: "finance",
    name: "weekly-finance",
    description: "Reconcile the bank export against the bills sheet",
    trigger: "Schedule 0 18 * * 0",
    human: "Sundays at 18:00",
    enabled: true,
    next: "Sun 9 Nov 18:00",
    last: "Sun 26 Oct 18:00",
    outcome: "failed",
    failures: 2,
    mode: "ask",
  },
  {
    id: "flights",
    name: "flight-watch",
    description: "Check MAD–LIS fares for March and tell me under 90 €",
    trigger: "Schedule 0 */6 * * *",
    human: "Every 6 hours",
    enabled: true,
    next: "Today 18:00",
    last: "Today 12:00",
    outcome: "no-work",
    mode: "read-only",
  },
  {
    id: "invoice",
    name: "invoice-arrived",
    description: "File a new invoice into Drive and add it to the bills sheet",
    trigger: "Signal at /signals/invoice-arrived",
    human: "When an invoice arrives",
    enabled: true,
    last: "Fri 31 Oct 09:12",
    outcome: "parked",
    mode: "ask",
  },
  {
    id: "garden",
    name: "plant-reminder",
    description: "Remind me to water the terrace in summer",
    trigger: "Schedule 0 20 * 6-9 *",
    human: "Daily at 20:00, June to September",
    enabled: false,
    mode: "read-only",
  },
]

export const ROUTINE_MD = `---
description: Reconcile the bank export against the bills sheet
schedule: 0 18 * * 0
mode: ask
---

Open the latest export in Drive/Bank, match each charge to a row in the
"Bills 2026" sheet, and list anything unmatched or over its usual amount.
`

export interface MemoryNode {
  node: string
  kind: "fact" | "episode"
  description: string
  date: string
  subjects: string[]
  source: "agent" | "recap" | "user"
}

export const MEMORY: MemoryNode[] = [
  {
    node: "f1",
    kind: "fact",
    description: "Jorge prefers aisle seats and flies Iberia when the fare is close",
    date: "2026-10-30",
    subjects: ["travel", "Iberia"],
    source: "recap",
  },
  {
    node: "e1",
    kind: "episode",
    description: "Compared two dentist quotes; picked Clínica Arturo Soria at 480 €",
    date: "2026-10-31",
    subjects: ["health", "dentist"],
    source: "agent",
  },
  {
    node: "f2",
    kind: "fact",
    description: "The car insurance with Mapfre renews on 15 November",
    date: "2026-10-28",
    subjects: ["car", "Mapfre"],
    source: "user",
  },
  {
    node: "f3",
    kind: "fact",
    description: "Ana's birthday is 12 December; she likes ceramics and hiking",
    date: "2026-10-20",
    subjects: ["Ana", "gifts"],
    source: "recap",
  },
  {
    node: "e2",
    kind: "episode",
    description: "Weekly finance check failed: the bank export was missing October",
    date: "2026-10-26",
    subjects: ["finance"],
    source: "recap",
  },
  {
    node: "f4",
    kind: "fact",
    description: "The landlord is Marta; rent goes out on the 1st by transfer",
    date: "2026-10-02",
    subjects: ["house", "landlord"],
    source: "agent",
  },
]

export const MEMORY_BODY =
  "Asked twice for an aisle seat on the Lisbon search, and turned down a 12 € cheaper TAP fare to stay on Iberia. Holds an Iberia Plus Silver card."

export const PROFILE_MD = `# Jorge

- Lives in Madrid, Arturo Soria. Works from home most days.
- Partner: Ana. Speaks Spanish and English.
- Car: 2019 Mazda 3, insured with Mapfre.
- Prefers short answers; a table when comparing more than two options.
`

export const STATS = {
  range: "30d",
  plans: "Claude runs: 4 in 5h · 21 in 7d",
  tiles: [
    { label: "Turns", value: "182", detail: "6 failed · 2 interrupted" },
    { label: "API cost", value: "$14.20", detail: "3.1M in · 410k out" },
    { label: "Plan runs", value: "47", detail: "Claude 41 · ChatGPT 6" },
    { label: "Ratings", value: "23 good", detail: "2 bad" },
  ],
  sources: [
    { source: "API", runs: 135, tokens: "3.5M", time: "4h 12m", cost: "$14.20" },
    { source: "Claude", runs: 41, tokens: "1.9M", time: "2h 03m", cost: "plan" },
    { source: "ChatGPT", runs: 6, tokens: "220k", time: "14m", cost: "plan" },
  ],
  origins: [
    { origin: "You, in the app", turns: 96, noWork: 0, failed: 2, cost: "$9.10" },
    { origin: "morning-brief", turns: 22, noWork: 1, failed: 0, cost: "$1.40" },
    { origin: "flight-watch", turns: 118, noWork: 112, failed: 1, cost: "$1.90" },
    { origin: "weekly-finance", turns: 4, noWork: 0, failed: 2, cost: "$0.60" },
    { origin: "Telegram", turns: 31, noWork: 0, failed: 1, cost: "$1.20" },
  ],
  tools: [
    { tool: "browser.navigate", calls: 412 },
    { tool: "memory.search", calls: 238 },
    { tool: "gmail.search", calls: 121 },
    { tool: "web.search", calls: 96 },
    { tool: "calendar.list", calls: 64 },
  ],
  memory: { searches: 238, opens: 91, remembered: 34, forgotten: 3 },
  approvals: { asked: 27, deniedPolicy: 4, deniedYou: 3 },
}

const DAYS = Array.from({ length: 30 }, (_, i) => i)
export const DAILY = DAYS.map((i) => {
  const base = 3 + ((i * 7) % 6)
  return {
    day: `${((i + 3) % 31) + 1}`,
    completed: base + (i % 5 === 0 ? 4 : 0),
    failed: i % 9 === 4 ? 2 : i % 6 === 0 ? 1 : 0,
    interrupted: i % 13 === 7 ? 1 : 0,
    cost: Number((0.25 + ((i * 13) % 9) * 0.06).toFixed(2)),
  }
})
