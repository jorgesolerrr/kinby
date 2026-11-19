// PROTOTYPE (#1450), throwaway. The shipped software factory's steps as fixed data, with the off
// review step, and one run whose implement attempt failed. The prompts and hooks are the real files,
// read raw by Vite.
import implementPrompt from "../../../../../src/kinby/factories/shipped/software/prompts/implement.md?raw"
import answerPrompt from "../../../../../src/kinby/factories/shipped/software/prompts/answer.md?raw"
import fixPrompt from "../../../../../src/kinby/factories/shipped/software/prompts/fix.md?raw"
import reviewPrompt from "../../../../../src/kinby/factories/shipped/software/prompts/review.md?raw"
import implementationHooks from "../../../../../src/kinby/factories/shipped/software/hooks/implementation.py?raw"
import reviewHooks from "../../../../../src/kinby/factories/shipped/software/hooks/review.py?raw"
import githubTools from "../../../../../src/kinby/factories/shipped/software/tools/github.py?raw"

export type StepKind = "agent" | "command" | "code" | "wait"

/** Where an outcome or a failure sends the run: a named end, the next step, or back to a step. */
export type Target = "next" | "done" | "stop" | { back: string; max: number }

export interface Step {
  id: string
  kind: StepKind
  description: string
  off?: boolean
  added?: boolean
  // agent
  engine?: string
  model?: string
  effort?: string
  timeout?: string
  resume?: string
  prompt?: string
  promptText?: string
  check?: string
  /** The check or call hook's edited source, once the user changed it. */
  hookText?: string
  // command
  run?: string[]
  // code
  call?: string
  // wait
  signal?: string
  quiet?: string
  // any
  retry?: number
  failed?: { back: string; max: number }
  outcomes?: Record<string, Target>
  requires?: string[]
  results?: Record<string, string>
}

/** The source of a hook by its function's name, from the file that defines it. */
export function hookSource(name: string): string {
  for (const file of [implementationHooks, reviewHooks, githubTools]) {
    const start = file.indexOf(`def ${name}(`)
    if (start === -1) continue
    const rest = file.slice(start)
    const end = rest.slice(1).search(/\n(def |class |@)/)
    return end === -1 ? rest : rest.slice(0, end + 1)
  }
  return `def ${name}(end: StepEnd) -> HookResult | None:\n    ...\n`
}

export const HOOK_FILE: Record<string, string> = {
  check_implementation: "hooks/implementation.py",
  check_answers: "hooks/implementation.py",
  read_review_verdict: "hooks/review.py",
  vet_ticket: "tools/github.py",
  publish_pull_request: "tools/github.py",
  assess_pull_request: "tools/github.py",
}

/** The checks a new agent step can pick, the factory's existing ones. */
export const CHECKS = ["check_implementation", "check_answers", "read_review_verdict"]
export const ENGINES = ["claude", "codex"]
export const MODELS: Record<string, string[]> = {
  claude: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
  codex: ["gpt-5.5-codex", "gpt-5.5-codex-mini"],
}
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"]
/** The workbench's command lists a command step can run. */
export const COMMANDS = ["setup", "checks"]

const BABYSIT_SIGNAL = `- headers.X-GitHub-Event: pull_request_review
  body.pull_request.number: "{{pr}}"
- headers.X-GitHub-Event: pull_request_review_comment
  body.pull_request.number: "{{pr}}"
- headers.X-GitHub-Event: check_suite
  body.action: completed
  body.check_suite.head_branch: "{{branch}}"
- headers.X-GitHub-Event: pull_request
  body.action: closed
  body.pull_request.number: "{{pr}}"
`

export const FACTORY = "software"

export const STEPS: Step[] = [
  {
    id: "ticket",
    kind: "code",
    description:
      "Passes the ticket on only once a trusted author wrote all its text. Records the commit the run worktree was cut from.",
    call: "vet_ticket",
    results: { base_commit: "str" },
  },
  {
    id: "setup",
    kind: "command",
    description:
      "Runs the workbench's setup list in the run worktree, which has installs of its own.",
    run: ["setup"],
  },
  {
    id: "implement",
    kind: "agent",
    description:
      "One coding session per issue writes the change. A ticket with nothing to do ends the run cancelled once the session wrote why as no_change.",
    engine: "claude",
    model: "claude-opus-5-5",
    effort: "high",
    prompt: "prompts/implement.md",
    promptText: implementPrompt,
    check: "check_implementation",
    requires: ["branch", "base"],
    results: { title: "str", body: "str", no_change: "str" },
    outcomes: { "no-change": "stop" },
    timeout: "60m",
  },
  {
    id: "answer",
    kind: "agent",
    description:
      "A fix round: answers the review threads trusted authors left on the pull request, commits its fixes, and rewrites the title and body.",
    engine: "claude",
    model: "claude-opus-5-5",
    effort: "high",
    resume: "implement",
    prompt: "prompts/answer.md",
    promptText: answerPrompt,
    check: "check_answers",
    requires: ["branch", "base"],
    results: { replies: "str", title: "str", body: "str" },
    timeout: "60m",
  },
  {
    id: "fix",
    kind: "agent",
    description:
      "A check repair: fixes what the failed checks sent back as results.failure, and changes nothing when nothing failed.",
    engine: "claude",
    model: "claude-opus-5-5",
    effort: "high",
    resume: "implement",
    prompt: "prompts/fix.md",
    promptText: fixPrompt,
    check: "check_implementation",
    requires: ["branch", "base"],
    results: { title: "str", body: "str" },
    timeout: "30m",
  },
  {
    id: "review",
    kind: "agent",
    off: true,
    description:
      "Adversarial review before the pull request opens. Off by default: reviewers on the pull request take its place.",
    engine: "claude",
    model: "claude-opus-5-5",
    effort: "high",
    prompt: "prompts/review.md",
    promptText: reviewPrompt,
    check: "read_review_verdict",
    requires: ["branch", "base"],
    results: { reviewed: "bool" },
    outcomes: { clean: "next", changes: { back: "fix", max: 2 } },
    timeout: "15m",
  },
  {
    id: "checks",
    kind: "command",
    description:
      "Runs setup again so a dependency change is checked against fresh installs, then the checks list. A failure goes back to fix.",
    run: ["setup", "checks"],
    failed: { back: "fix", max: 2 },
  },
  {
    id: "publish",
    kind: "code",
    description:
      "Pushes the branch. The first time it opens the pull request; after a fix round it applies the new title, body and replies. Never retried.",
    call: "publish_pull_request",
    requires: ["branch", "base"],
    results: { pr: "int" },
    retry: 0,
  },
  {
    id: "babysit",
    kind: "wait",
    description:
      "Holds nothing while it waits for a review, a reply on a thread, a finished check suite, or the pull request closing.",
    signal: BABYSIT_SIGNAL,
    quiet: "20m",
    results: { quiet: "bool" },
  },
  {
    id: "assess",
    kind: "code",
    description:
      "Reads the pull request: merge-ready ends the run done; threads start a fix round; failed checks start a repair; anything else waits again.",
    call: "assess_pull_request",
    requires: ["pr"],
    results: { feedback: "str", failure: "str", pr: "int", merge_ready: "bool" },
    outcomes: {
      "merge-ready": "done",
      merged: "done",
      closed: "stop",
      feedback: { back: "answer", max: 3 },
      "checks-failed": { back: "answer", max: 2 },
      waiting: { back: "babysit", max: 30 },
    },
  },
]

/** Values the factory itself provides before any step runs. */
export const GIVEN = ["branch", "base", "issue"]

export interface Attempt {
  n: number
  step: string
  ending: "clean" | "failed" | "timed-out"
  started: string
  took: string
  cost?: string
  summary: string
}

export const RUN = {
  id: "r-1412",
  item: "Issue #1412: Show a line's daily budget on its card",
  line: "software on kinby",
  status: "Needs you",
  step: "implement",
  attempts: [
    {
      n: 1,
      step: "ticket",
      ending: "clean",
      started: "09:02",
      took: "4s",
      summary: "Trusted author. base_commit 3f9a2c1.",
    },
    {
      n: 1,
      step: "setup",
      ending: "clean",
      started: "09:02",
      took: "1m 12s",
      summary: "bun install, uv sync",
    },
    {
      n: 1,
      step: "implement",
      ending: "timed-out",
      started: "09:03",
      took: "60m",
      cost: "$4.10",
      summary: "The session ran past its 60m timeout while the test suite ran on the bind mount.",
    },
    {
      n: 2,
      step: "implement",
      ending: "failed",
      started: "10:04",
      took: "38m",
      cost: "$3.22",
      summary:
        "check_implementation: no commit on agent/1412-line-daily-budget past base_commit, and no .scratch/no-change.md.",
    },
  ] satisfies Attempt[],
}
