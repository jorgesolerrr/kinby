import type { InstanceSummary, ProcessState } from "@kinby/contract"

import type { InstanceTab } from "@/lib/selection"

/** An instance's tabs, by their labels, in the order its page lists them. */
export const INSTANCE_TABS: { id: InstanceTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "config", label: "Config" },
  { id: "routines", label: "Routines" },
  { id: "memory", label: "Memory" },
  { id: "stats", label: "Stats" },
]

export type InstanceNotice = InstanceSummary["notices"][number]

const NOTICE_TITLES: Record<InstanceNotice["code"], string> = {
  revision_behind: "Behind the hub",
  template_behind: "Behind the factory",
  hub_route_missing: "No hub route",
  mcp_server_failed: "MCP server failed",
}

/** What a notice the hub reports about an instance is called. A failed MCP server is named. */
export function noticeTitle(notice: InstanceNotice): string {
  return notice.code === "mcp_server_failed"
    ? `MCP server ${notice.server} failed`
    : NOTICE_TITLES[notice.code]
}

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
