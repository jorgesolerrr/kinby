// PROTOTYPE (#1450), throwaway. The variants' shared helpers that aren't components: how a route
// reads, which text editors a step has, a new step's defaults, and save-then-retry.
import { useState } from "react"

import { HOOK_FILE, hookSource, type Step, type StepKind, type Target } from "./data"
import { go } from "./nav"
import { retryRun } from "./store"

/** How a route reads in words: "back to babysit, up to 30 times". */
export function targetText(target: Target): string {
  if (typeof target === "string") return target === "next" ? "next step" : target
  return `back to ${target.back}, up to ${target.max} ${target.max === 1 ? "time" : "times"}`
}

/** The step's text editors: its prompt, its check or call hook, or its signal patterns. */
export function stepTexts(draft: Step): {
  key: string
  label: string
  file: string
  language: "markdown" | "python" | "yaml"
  value: string
  put: (value: string) => Partial<Step>
}[] {
  const texts = []
  if (draft.kind === "agent") {
    texts.push({
      key: "prompt",
      label: "Prompt",
      file: draft.prompt ?? `prompts/${draft.id}.md`,
      language: "markdown" as const,
      value: draft.promptText ?? "",
      put: (promptText: string) => ({ promptText }),
    })
  }
  const hook = draft.check ?? draft.call
  if (hook !== undefined) {
    texts.push({
      key: "hook",
      label: draft.check !== undefined ? `Check · ${hook}` : `Call · ${hook}`,
      file: HOOK_FILE[hook] ?? "hooks/steps.py",
      language: "python" as const,
      value: draft.hookText ?? hookSource(hook),
      put: (hookText: string) => ({ hookText }),
    })
  }
  if (draft.kind === "wait") {
    texts.push({
      key: "signal",
      label: "Signal",
      file: "signal",
      language: "yaml" as const,
      value: draft.signal ?? "",
      put: (signal: string) => ({ signal }),
    })
  }
  return texts
}

/** Whether the draft differs from the saved step, apart from on/off, which saves at once. */
export function isDirty(draft: Step, step: Step): boolean {
  return JSON.stringify({ ...draft, off: step.off }) !== JSON.stringify(step)
}

/** What a new step needs before it is saved: its kind, id, description and kind's first fields. */
function newStep(kind: StepKind, id: string, description: string, check: string): Step {
  if (kind === "agent") {
    return {
      id,
      kind,
      description,
      engine: "claude",
      model: "claude-opus-5-5",
      effort: "high",
      timeout: "30m",
      prompt: `prompts/${id}.md`,
      promptText: "",
      check: check === "none" ? undefined : check,
      requires: ["branch", "base"],
    }
  }
  if (kind === "command") return { id, kind, description, run: ["checks"] }
  return { id, kind, description, signal: "- headers.X-GitHub-Event: \n", quiet: "20m" }
}

/** The add-step form's own state. */
export function useNewStep() {
  const [kind, setKind] = useState<StepKind>("agent")
  const [id, setId] = useState("")
  const [description, setDescription] = useState("")
  const [check, setCheck] = useState("none")
  const [run, setRun] = useState("checks")
  const step = () => {
    const made = newStep(kind, id, description, check)
    return kind === "command" ? { ...made, run: [run] } : made
  }
  return {
    fields: { kind, setKind, id, setId, description, setDescription, check, setCheck, run, setRun },
    ready: /^[a-z][a-z0-9-]*$/.test(id) && description.trim() !== "",
    step,
  }
}

export function saveAndRetry(save: () => void) {
  save()
  retryRun()
  go("run", null)
}
