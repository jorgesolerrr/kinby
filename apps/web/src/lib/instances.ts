import type { InstanceSummary } from "@kinby/contract"

/** The persona name the user gave the instance, or its manifest id when it has none. */
export function instanceName(instance: InstanceSummary): string {
  return instance.persona_name ?? instance.manifest_id
}
