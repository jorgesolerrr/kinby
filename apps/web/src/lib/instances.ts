import type { InstanceSummary, ProcessState } from "@kinby/contract"

/** The persona name the user gave the instance, or its manifest id when it has none. */
export function instanceName(instance: InstanceSummary): string {
  return instance.persona_name ?? instance.manifest_id
}

/** What the runtime saw the instance doing when it was listed. A container in a restart loop reads as restarting. */
export function observedState(instance: InstanceSummary): ProcessState | "restarting" {
  return instance.process === "starting" && instance.detail === "restarting"
    ? "restarting"
    : instance.process
}

/**
 * What the sidebar says the instance is doing. One the user stopped reads stopped when its
 * container is down for any reason, a crash included. The process reads as observed otherwise.
 */
export function shownState(instance: InstanceSummary): ProcessState | "restarting" {
  const down = ["stopped", "failed", "created", "missing"].includes(instance.process)
  return instance.intended_state === "stopped" && down ? "stopped" : observedState(instance)
}
