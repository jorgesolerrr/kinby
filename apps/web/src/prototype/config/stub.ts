// PROTOTYPE, throwaway. A stub instance started from the software factory package.

export const CONFIG_FILES = [
  "SYSTEM.md",
  "RECAP.md",
  "permissions.toml",
  "kinby.toml",
  "package.yaml",
] as const
export type ConfigFile = (typeof CONFIG_FILES)[number]

export const FILE_LABEL: Record<ConfigFile, string> = {
  "SYSTEM.md": "Behavior prompt",
  "RECAP.md": "Recap prompt",
  "permissions.toml": "Permissions",
  "kinby.toml": "Manifest",
  "package.yaml": "Package config",
}

export const INSTANCE = {
  id: "inst_7f3a",
  name: "Coder",
  hubRevision: "3f1c9e2",
  revision: "a81d044",
  package: { id: "coder", initialized_version: "0.3.0", installed_version: "0.4.1" },
}

export const FILES: Record<ConfigFile, string> = {
  "SYSTEM.md": `You are Coder, Jorge's coding teammate on the kinby repository.

Work one ticket at a time. Keep diffs small, follow CODING-STANDARD.md,
and never merge on your own.
`,
  "RECAP.md": `Summarize what happened in this thread for future you.
Keep decisions, open questions, and names of files touched.
`,
  "permissions.toml": `mode = "ask"
ceiling = "auto"

[tools]
shell = "ask"
routine_write = "ask"
remember = "allow"
`,
  "kinby.toml": `id = "coder"
state_dir = "/instance/state"

[models]
main = "anthropic:claude-opus-5-5"
recap = "anthropic:claude-haiku-4-5"

[budgets]
usd_per_day = 20

[routines]
timezone = "Europe/Madrid"

[serve]
listen = "0.0.0.0:8080"
`,
  "package.yaml": `check_commands:
  - bun run check
skills:
  implement: tdd
  review: adversarial-review
`,
}

export type Routine = {
  name: string
  description: string
  schedule: string | null
  enabled: boolean
  mode: string
  next_run: string | null
  last_run: string | null
  failure_count: number
  pending: number
  signal: boolean
  content: string
}

export const ROUTINES: Routine[] = [
  {
    name: "pick-ticket",
    description: "Take the next ready-for-agent ticket and start the factory.",
    schedule: null,
    enabled: true,
    mode: "auto",
    next_run: null,
    last_run: "today 10:42",
    failure_count: 0,
    pending: 0,
    signal: true,
    content: `---
description: Take the next ready-for-agent ticket and start the factory.
enabled: true
mode: auto
signal:
  auth: hmac
---
A ticket was labelled ready-for-agent. Pick it up.
`,
  },
  {
    name: "morning-digest",
    description: "Summarize open PRs and CI state.",
    schedule: "0 8 * * 1-5",
    enabled: true,
    mode: "read-only",
    next_run: "Mon 08:00",
    last_run: "Fri 08:00",
    failure_count: 0,
    pending: 0,
    signal: false,
    content: `---
description: Summarize open PRs and CI state.
schedule: "0 8 * * 1-5"
enabled: true
mode: read-only
---
List open PRs, their checks, and anything waiting on me.
`,
  },
  {
    name: "dependency-bump",
    description: "Open a PR bumping outdated dependencies.",
    schedule: "0 3 * * 0",
    enabled: false,
    mode: "auto",
    next_run: null,
    last_run: "Sun 03:00",
    failure_count: 10,
    pending: 0,
    signal: false,
    content: `---
description: Open a PR bumping outdated dependencies.
schedule: "0 3 * * 0"
enabled: false
mode: auto
---
Run uv lock --upgrade and bun update, then open a PR.
`,
  },
]

export type Tier = "instance" | "package" | "workspace"

export type Skill = { name: string; tier: Tier; description: string; content: string }

export const SKILLS: Skill[] = [
  {
    name: "tdd",
    tier: "instance",
    description: "Red, green, refactor, customized for this repo.",
    content: "---\nname: tdd\n---\nWrite the failing test first. Run only the touched test file.\n",
  },
  {
    name: "tdd",
    tier: "package",
    description: "Test-driven development.",
    content: "---\nname: tdd\n---\nWrite the failing test first.\n",
  },
  {
    name: "adversarial-review",
    tier: "package",
    description: "Two-axis review by the other model.",
    content: "---\nname: adversarial-review\n---\nReview against standards and spec.\n",
  },
  {
    name: "shadcn",
    tier: "workspace",
    description: "Manages shadcn components.",
    content: "---\nname: shadcn\n---\nUse the CLI to add components.\n",
  },
]

export type Tool = { name: string; source: string; writes: boolean }

export const TOOLS: Tool[] = [
  { name: "shell", source: "core", writes: true },
  { name: "read_file", source: "core", writes: false },
  { name: "remember", source: "core", writes: true },
  { name: "routine_write", source: "core (instance tools)", writes: true },
  { name: "code", source: "package coder 0.4.1", writes: true },
  { name: "gh_issue", source: "tools/gh_issue.py", writes: true },
]

export type Secret = { name: string; required: boolean; is_set: boolean }

export const SECRETS: Secret[] = [
  { name: "ANTHROPIC_API_KEY", required: true, is_set: true },
  { name: "GITHUB_TOKEN", required: true, is_set: true },
  { name: "SIGNAL_SECRET", required: false, is_set: false },
]

export const LOGINS = [
  { name: "Codex", status: "complete" as const },
  { name: "Claude Code", status: "expired" as const },
]

export const MODES = ["read-only", "ask", "auto", "full-access"] as const
export type Mode = (typeof MODES)[number]
export const MODE_HINT: Record<Mode, string> = {
  "read-only": "reads only, never writes",
  ask: "asks before every write",
  auto: "writes inside the workspace, asks for the rest",
  "full-access": "never asks",
}
export type Rule = "allow" | "ask" | "deny"

export type Permissions = {
  mode: Mode
  ceiling: Mode
  tools: Record<string, Rule>
  bash: { deny: string[]; ask: string[] }
}

export const SHIPPED_BASH_DENY = ["rm -rf /*", "git push --force*", "sudo *"]

export const PERMISSIONS: Permissions = {
  mode: "ask",
  ceiling: "auto",
  tools: { shell: "ask", routine_write: "ask", remember: "allow" },
  bash: { deny: [...SHIPPED_BASH_DENY, "gh pr merge*"], ask: ["git push*"] },
}
