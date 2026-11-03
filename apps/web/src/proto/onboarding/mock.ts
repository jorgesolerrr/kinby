// PROTOTYPE: static mock data for /proto/onboarding. Throwaway.
import type { Avatar } from "@kinby/contract"

import { INSTANCES, type MockInstance } from "../shell/mock"
import { ALL } from "../library/mock"

/** The screens this prototype covers, in the order the bar lists them. */
export const SCREENS = [
  { id: "sign-in", label: "Sign-in" },
  { id: "first-run", label: "First run: only the home instance" },
  { id: "new-instance", label: "New instance" },
  { id: "no-threads", label: "Instance with no threads" },
  { id: "stopped", label: "Stopped instance" },
  { id: "empty-library", label: "Empty Library tab" },
] as const

export type ScreenId = (typeof SCREENS)[number]["id"]

// ── The shell's instances, per screen ──────────────────────────────────────────────────────────

const FULL = INSTANCES.map((i) => ({ ...i }))
const FULL_LIBRARY = [...ALL]

const ATLAS: MockInstance = {
  id: "atlas",
  name: "Atlas",
  avatar: { shape: "squircle", color: "red" },
  state: "running",
  views: [],
  threads: [],
}

/** The home instance on a hub's first start: no threads, its API key waiting. */
const FRESH_HOME: MockInstance = {
  ...FULL[0],
  name: "Kinby",
  threads: [],
  views: [],
}

/**
 * Swaps the shell's instances and the library's entries in place for a screen. The shell reads
 * the module arrays, so the caller remounts it after.
 */
export function stage(screen: ScreenId) {
  const instances =
    screen === "first-run" ? [FRESH_HOME] : screen === "no-threads" ? [...FULL, ATLAS] : FULL
  INSTANCES.splice(0, INSTANCES.length, ...instances)
  // A fresh hub's library: only what kinby ships, and no MCP server.
  const library =
    screen === "empty-library"
      ? FULL_LIBRARY.filter((e) => e.kind !== "mcp_server" && e.source === "shipped")
      : FULL_LIBRARY
  ALL.splice(0, ALL.length, ...library)
}

/** Which instance a screen opens on. */
export const TARGET: Record<ScreenId, string> = {
  "sign-in": "kin",
  "first-run": "kin",
  "new-instance": "kin",
  "no-threads": "atlas",
  stopped: "ledger",
  "empty-library": "kin",
}

export const ATLAS_SKILLS = ["trip-planner", "research"]

// ── Onboarding ─────────────────────────────────────────────────────────────────────────────────

export interface Role {
  id: string
  name: string
  factory?: string
  lead: string
  picks: string[]
  proposedName: string
  avatar: Avatar
  prompt: string
}

const VANILLA_PROMPT = `You are a personal teammate. You remember what the user tells you, act on their behalf with the tools you hold, and ask before anything you cannot undo.

Answer plainly and briefly. When a task needs more than one step, say what you will do, then do it.`

const CODER_PROMPT = `You are the coder in the software factory. You take one ticket at a time, read the repository before you change it, and leave a branch whose checks pass.

Follow the repository's own AGENTS.md. Ask on the ticket when its question is unclear; never guess a requirement.`

export const ROLES: Role[] = [
  {
    id: "none",
    name: "None",
    lead: "A vanilla instance, from kinby's built-in defaults. Kinby is one.",
    picks: [],
    proposedName: "",
    avatar: { shape: "circle", color: "blue" },
    prompt: VANILLA_PROMPT,
  },
  {
    id: "software/coder",
    name: "coder",
    factory: "software",
    lead: "Turns GitHub issues labeled ready-for-agent into pull requests.",
    picks: ["adversarial-review", "codebase-design", "pr", "tdd", "unslop"],
    proposedName: "Coder 2",
    avatar: { shape: "square", color: "blue" },
    prompt: CODER_PROMPT,
  },
]

export interface OfferedSkill {
  name: string
  description: string
  shipped?: boolean
}

export const OFFERED_SKILLS: OfferedSkill[] = [
  {
    name: "trip-planner",
    description: "Plan a trip end to end: dates, flights, places to stay, a day-by-day outline.",
  },
  {
    name: "research",
    description: "Investigate a question against primary sources and write up the findings.",
  },
  {
    name: "paper-digest",
    description: "Read a paper and write a one-page digest: claim, method, evidence.",
  },
  {
    name: "grill-with-docs",
    description: "Grilling that records settled terms in a glossary and decisions as ADRs.",
  },
  {
    name: "adversarial-review",
    description: "Review a diff on two axes, standards and spec, from a fresh context.",
    shipped: true,
  },
  {
    name: "codebase-design",
    description: "Deep-module vocabulary: module, interface, depth, seam, adapter.",
    shipped: true,
  },
  { name: "pr", description: "Write a pull request body.", shipped: true },
  { name: "tdd", description: "Test-driven development, red-green-refactor.", shipped: true },
  { name: "unslop", description: "Cut AI tells from prose a human will read.", shipped: true },
]

export type FieldKind = "choice" | "secret" | "url" | "text" | "login"

export interface MockField {
  name: string
  label: string
  description: string
  kind: FieldKind
  required: boolean
  /** Who asks for it: kinby itself, or the role. */
  askedBy: "kinby" | string
  options?: string[]
  initial?: string
  /** A hub secret that can fill it. */
  hubSecret?: string
}

const BUILT_IN: MockField[] = [
  {
    name: "model",
    label: "Model",
    description: "The model every turn runs on.",
    kind: "choice",
    required: true,
    askedBy: "kinby",
    options: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
    initial: "claude-sonnet-5-5",
  },
  {
    name: "api_key",
    label: "API key",
    description: "The key for the model's provider.",
    kind: "secret",
    required: true,
    askedBy: "kinby",
    hubSecret: "ANTHROPIC_API_KEY",
  },
]

const CODER_FIELDS: MockField[] = [
  {
    name: "repository",
    label: "Repository",
    description: "The GitHub repository the coder works in.",
    kind: "url",
    required: true,
    askedBy: "coder",
  },
  {
    name: "github_token",
    label: "GitHub token",
    description: "Reads issues and opens pull requests on the repository.",
    kind: "secret",
    required: true,
    askedBy: "coder",
    hubSecret: "GITHUB_TOKEN",
  },
  {
    name: "webhook_secret",
    label: "Webhook secret",
    description: "Checks that a signal comes from GitHub.",
    kind: "secret",
    required: false,
    askedBy: "coder",
  },
  {
    name: "claude_code",
    label: "Claude Code",
    description: "The coder runs Claude Code on your subscription. You sign in once it exists.",
    kind: "login",
    required: false,
    askedBy: "coder",
  },
]

export const fieldsFor = (role: Role): MockField[] =>
  role.factory === undefined ? BUILT_IN : [...BUILT_IN, ...CODER_FIELDS]

export const IMAGE_STEPS = ["Read the checkout", "Build the image", "Read what it asks for"]

export const CREATE_STEPS = [
  "Check the setup",
  "Copy the role's files",
  "Copy the picked skills",
  "Create the container",
  "List the instance",
]

export const LOGIN = { url: "https://claude.ai/oauth/device", code: "KQPW-7HTM" }
