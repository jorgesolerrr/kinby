import { CallError } from "@kinby/contract"
import type {
  ConfigActor,
  ConfigChange,
  InstanceClient,
  PromptName,
  PromptResult,
} from "@kinby/contract"

/** The file each prompt is kept in, as the config changes name it. */
export const PROMPT_FILES: Record<PromptName, string> = {
  behavior: "SYSTEM.md",
  recap: "RECAP.md",
}

/** A prompt as the instance has it, and the latest change to its file. */
export interface OpenedPrompt {
  prompt: PromptResult
  lastChange: ConfigChange | undefined
}

/** A save the instance took, or one it refused because the file changed since it was read. */
export type Saved = { state: "saved"; opened: OpenedPrompt } | { state: "stale" }

type Caller = Pick<InstanceClient, "call">

export async function openPrompt(caller: Caller, name: PromptName): Promise<OpenedPrompt> {
  const [prompt, lastChange] = await Promise.all([
    caller.call("prompt.get", { name }),
    latestChange(caller, name),
  ])
  return { prompt, lastChange }
}

/** Write `content` over the prompt read with `hash`. */
export async function savePrompt(
  caller: Caller,
  name: PromptName,
  content: string,
  hash: string,
): Promise<Saved> {
  let prompt: PromptResult
  try {
    prompt = await caller.call("prompt.set", { name, content, hash })
  } catch (error) {
    if (error instanceof CallError && error.code === "STALE") return { state: "stale" }
    throw error
  }
  return { state: "saved", opened: { prompt, lastChange: await latestChange(caller, name) } }
}

const ACTORS: Record<ConfigActor, string> = {
  app: "you",
  agent: "the agent",
  failure_policy: "the routine failure policy",
}

const WHEN = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" })

/** Who changed a file last and when, or that nobody has since the log began. */
export function lastChanged(change: ConfigChange | undefined): string {
  if (change === undefined) return "Never changed"
  return `Last changed by ${ACTORS[change.actor]}, ${WHEN.format(new Date(change.at))}`
}

async function latestChange(caller: Caller, name: PromptName): Promise<ConfigChange | undefined> {
  const history = await caller.call("config.history", { file: PROMPT_FILES[name], limit: 1 })
  return history.changes[0]
}
