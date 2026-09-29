import { CallError } from "@kinby/contract"
import type {
  ConfigChange,
  InstanceClient,
  SkillListResult,
  SkillResult,
  SkillSummary,
  SkillTier,
} from "@kinby/contract"

import { latestChange } from "@/lib/config-changes"

type Caller = Pick<InstanceClient, "call">

/** A skill as the instance has it, and, for an instance skill, the latest change to it. */
export interface OpenedSkill {
  skill: SkillResult
  lastChange: ConfigChange | undefined
}

/** The skills that share a name: the one the model reads, then those it hides. */
export interface SkillGroup {
  winner: SkillSummary
  shadowed: SkillSummary[]
}

/** A write the instance refused: the skill changed since it was read, or a value is wrong. */
type Refused = { state: "stale" } | { state: "invalid"; fields: Record<string, string> }

export type Written = { state: "saved"; opened: OpenedSkill } | Refused

export type Removed = { state: "removed"; listed: SkillListResult } | Refused

/** The file an instance skill's config changes name. */
export function skillFile(name: string): string {
  return `skills/${name}`
}

/** Group the list by name. It comes sorted by name, each name's skills in tier order. */
export function byName(skills: SkillSummary[]): SkillGroup[] {
  const groups: SkillGroup[] = []
  for (const skill of skills) {
    const last = groups.at(-1)
    if (last?.winner.name === skill.name) last.shadowed.push(skill)
    else groups.push({ winner: skill, shadowed: [] })
  }
  return groups
}

export async function openSkill(
  caller: Caller,
  name: string,
  tier: SkillTier,
): Promise<OpenedSkill> {
  const [skill, lastChange] = await Promise.all([
    caller.call("skill.read", { name, tier }),
    tier === "instance" ? latestChange(caller, skillFile(name)) : undefined,
  ])
  return { skill, lastChange }
}

/** Write an instance skill's SKILL.md over the one read with `hash`, or create it with null. */
export async function writeSkill(
  caller: Caller,
  name: string,
  content: string,
  hash: string | null,
): Promise<Written> {
  let skill: SkillResult
  try {
    skill = await caller.call("skill.write", { name, content, hash })
  } catch (error) {
    return refused(error)
  }
  return {
    state: "saved",
    opened: { skill, lastChange: await latestChange(caller, skillFile(name)) },
  }
}

/** Delete the instance skill read with `hash`, bringing back the one it hid. */
export async function removeSkill(caller: Caller, name: string, hash: string): Promise<Removed> {
  try {
    return { state: "removed", listed: await caller.call("skill.delete", { name, hash }) }
  } catch (error) {
    return refused(error)
  }
}

function refused(error: unknown): Refused {
  if (error instanceof CallError && error.code === "STALE") return { state: "stale" }
  if (error instanceof CallError && error.code === "INVALID_ARGUMENT") {
    return { state: "invalid", fields: error.fields }
  }
  throw error
}
