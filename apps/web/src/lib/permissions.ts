import type {
  ConfigChange,
  GateAction,
  InstanceClient,
  PermissionMode,
  PermissionsResult,
} from "@kinby/contract"

import { latestChange } from "@/lib/config-changes"
import { type Refused, refusal } from "@/lib/config-writes"

export const PERMISSIONS_FILE = "permissions.toml"

/** The modes from the narrowest to the widest. */
export const MODES: PermissionMode[] = ["read-only", "ask", "auto", "full-access"]

/** The permissions as the panel edits them, with only the instance's own deny patterns. */
export interface PermissionsDraft {
  mode: PermissionMode
  ceiling: PermissionMode
  /** The tools with a rule of their own. Every other tool follows the mode. */
  tools: Record<string, GateAction>
  deny: string[]
  ask: string[]
}

/** The permissions as the instance has them, and the latest change to the file. */
export interface OpenedPermissions {
  draft: PermissionsDraft
  /** kinby's deny patterns, which always apply. */
  shipped: string[]
  hash: string
  lastChange: ConfigChange | undefined
}

export type SavedPermissions = { state: "saved"; opened: OpenedPermissions } | Refused

type Caller = Pick<InstanceClient, "call">

export function aboveCeiling(mode: PermissionMode, ceiling: PermissionMode): boolean {
  return MODES.indexOf(mode) > MODES.indexOf(ceiling)
}

export async function openPermissions(caller: Caller): Promise<OpenedPermissions> {
  const [permissions, lastChange] = await Promise.all([
    caller.call("permissions.get", {}),
    latestChange(caller, PERMISSIONS_FILE),
  ])
  return opened(permissions, lastChange)
}

/** Write `draft` over the permissions read with `hash`. */
export async function savePermissions(
  caller: Caller,
  { mode, ceiling, tools, deny, ask }: PermissionsDraft,
  hash: string,
): Promise<SavedPermissions> {
  let permissions: PermissionsResult
  try {
    permissions = await caller.call("permissions.set", {
      mode,
      ceiling,
      tools,
      bash: { deny, ask },
      hash,
    })
  } catch (error) {
    return refusal(error)
  }
  return {
    state: "saved",
    opened: opened(permissions, await latestChange(caller, PERMISSIONS_FILE)),
  }
}

function opened(
  { mode, ceiling, tools, bash, hash }: PermissionsResult,
  lastChange: ConfigChange | undefined,
): OpenedPermissions {
  const patterns = (shipped: boolean) =>
    bash.deny.filter((deny) => deny.shipped === shipped).map((deny) => deny.pattern)
  return {
    draft: { mode, ceiling, tools, deny: patterns(false), ask: bash.ask },
    shipped: patterns(true),
    hash,
    lastChange,
  }
}
