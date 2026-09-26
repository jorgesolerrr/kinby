// PROTOTYPE, throwaway. Stub data for the create-instance flow; nothing here talks to a hub.

export type FieldKind = "config" | "secret"

export interface SetupField {
  name: string
  label: string
  description: string
  kind: FieldKind
  required: boolean
  default?: string
  multiline?: boolean
}

export interface SubscriptionLogin {
  id: string
  label: string
  description: string
}

export interface PackageCard {
  id: string
  displayName: string
  distribution: string | null
  version: string | null
  description: string
  icon: string
  available: boolean
  fields: SetupField[]
  logins: SubscriptionLogin[]
}

const modelField: SetupField = {
  name: "model",
  label: "Model",
  description: "The model every turn runs on unless a routine names another.",
  kind: "config",
  required: true,
  default: "claude-sonnet-5",
}

const apiKeyField: SetupField = {
  name: "ANTHROPIC_API_KEY",
  label: "Anthropic API key",
  description: "Pays for the instance's own turns. Stored per instance, never shown again.",
  kind: "secret",
  required: true,
}

export const VANILLA: PackageCard = {
  id: "vanilla",
  displayName: "Vanilla",
  distribution: null,
  version: null,
  description: "kinby's built-in defaults. You write the behavior prompt and pick tools later.",
  icon: "✦",
  available: true,
  fields: [modelField, apiKeyField],
  logins: [],
}

export const PACKAGES: PackageCard[] = [
  {
    id: "coder",
    displayName: "Software factory",
    distribution: "kinby-coder",
    version: "0.4.1",
    description:
      "Picks issues marked ready-for-agent, codes them with Codex, reviews with Claude, opens PRs.",
    icon: "⌘",
    available: true,
    fields: [
      modelField,
      apiKeyField,
      {
        name: "repository",
        label: "Repository",
        description: "The GitHub repository the factory works on, as owner/name.",
        kind: "config",
        required: true,
      },
      {
        name: "check_command",
        label: "Check command",
        description: "What must pass before a PR opens. Goes to factory.toml.",
        kind: "config",
        required: true,
        default: "bun run check",
      },
      {
        name: "commit_identity",
        label: "Commit identity",
        description: "Name and email on the factory's commits.",
        kind: "config",
        required: true,
        default: "kinby-coder <coder@kinby.local>",
      },
      {
        name: "GITHUB_TOKEN",
        label: "GitHub token",
        description: "Fine-grained, with contents, issues, and pull requests on the repository.",
        kind: "secret",
        required: true,
      },
    ],
    logins: [
      { id: "codex", label: "Codex", description: "ChatGPT subscription, for coding runs." },
      {
        id: "claude-code",
        label: "Claude Code",
        description: "Claude subscription, for adversarial review.",
      },
    ],
  },
  {
    id: "content",
    displayName: "Content factory",
    distribution: null,
    version: null,
    description: "Drafts, edits, and schedules posts from your notes.",
    icon: "✎",
    available: false,
    fields: [],
    logins: [],
  },
  {
    id: "life",
    displayName: "Life mate",
    distribution: null,
    version: null,
    description: "Calendar, reminders, and a daily brief.",
    icon: "☀",
    available: false,
    fields: [],
    logins: [],
  },
]

export const ALL = [VANILLA, ...PACKAGES]

export function packageById(id: string): PackageCard {
  return ALL.find((card) => card.id === id) ?? VANILLA
}

export const SHAPES = ["circle", "squircle", "square"] as const
export type Shape = (typeof SHAPES)[number]

export const COLORS = [
  "oklch(0.65 0.19 25)",
  "oklch(0.72 0.16 70)",
  "oklch(0.68 0.15 150)",
  "oklch(0.62 0.14 230)",
  "oklch(0.58 0.2 290)",
  "oklch(0.45 0 0)",
]

// The steps the hub reports on the create operation, in order.
export const CREATE_STEPS = [
  "build image",
  "inspect descriptor",
  "validate setup",
  "initialize",
  "publish",
]
export const PREPARE_IMAGE_STEPS = ["build image", "inspect descriptor"]
export const INITIALIZE_STEPS = ["validate setup", "initialize", "publish"]
export const START_STEPS = ["start container", "wait for health"]

export const FAILURE = {
  step: "validate setup",
  detail: "GITHUB_TOKEN cannot read jorgesolerrr/kinby: the token lacks the contents permission.",
  field: "GITHUB_TOKEN",
}

export const EXISTING_INSTANCES = [
  { instance_id: "home", persona_name: "Home", intended_state: "running" },
  { instance_id: "coder", persona_name: "kinby coder", intended_state: "running" },
]
