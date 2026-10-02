import type { ConfigChange, InstanceClient, ProfileResult } from "@kinby/contract"

import { latestChange, readWithChange, unlessStale } from "@/lib/config-changes"

/** The profile's file, as the config changes name it. */
export const PROFILE_FILE = "memory/profile.md"

/** The profile as the instance has it, and the latest change to its file. */
export interface OpenedProfile {
  profile: ProfileResult
  lastChange: ConfigChange | undefined
}

/** A save the instance took, or one it refused because the file changed since it was read. */
export type SavedProfile = { state: "saved"; opened: OpenedProfile } | { state: "stale" }

type Caller = Pick<InstanceClient, "call">

/**
 * About how many tokens `text` takes in a prompt: a quarter of its characters, rounded up. It
 * counts code points, not UTF-16 units, so it matches the count the instance sends.
 */
export function tokens(text: string): number {
  return Math.ceil(Array.from(text).length / 4)
}

export async function openProfile(caller: Caller): Promise<OpenedProfile> {
  const [profile, lastChange] = await readWithChange(caller, PROFILE_FILE, () =>
    caller.call("profile.get", {}),
  )
  return { profile, lastChange }
}

/** Write `text` over the profile read with `hash`. */
export async function saveProfile(
  caller: Caller,
  text: string,
  hash: string,
): Promise<SavedProfile> {
  const profile = await unlessStale(caller.call("profile.set", { text, hash }))
  if (profile === "stale") return { state: "stale" }
  return {
    state: "saved",
    opened: { profile, lastChange: await latestChange(caller, PROFILE_FILE) },
  }
}
