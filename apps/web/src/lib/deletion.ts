import type {
  Client,
  Clock,
  InstanceDeleteCommand,
  InstanceDeletePreviewResult,
  OperationStep,
} from "@kinby/contract"

import { finished, pace, reason } from "@/lib/operation"

/**
 * How a permanent deletion of a removed instance stands. `changed` means the hub refused because
 * the targets changed since the preview, and `preview` is what it would delete now.
 */
export type Deleting =
  | { state: "deleting"; steps: OperationStep[] }
  | { state: "changed"; preview: InstanceDeletePreviewResult }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "deleted" }

/**
 * Delete the previewed targets and report each step the hub reaches. A deletion that failed before
 * deleting anything is previewed again, and reports `changed` when the targets differ from the ones
 * it sent. The returned function stops following it; the hub carries on.
 */
export function followDeletion(
  caller: Pick<Client, "call">,
  command: InstanceDeleteCommand,
  report: (deleting: Deleting) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (deleting: Deleting) => {
    if (!pacing.stopped) report(deleting)
  }

  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const accepted = await caller.call("instance.delete", command)
      const operation = await finished(caller, accepted.operation_id, pacing, (reached) => {
        steps = reached
        emit({ state: "deleting", steps })
      })
      if (operation.state === "succeeded") return emit({ state: "deleted" })
      steps = operation.steps ?? []
      if (!steps.some((step) => step.state === "succeeded")) {
        const preview = await caller.call("instance.delete.preview", {
          instance_id: command.instance_id,
        })
        if (!sameTargets(preview, command)) return emit({ state: "changed", preview })
      }
      emit({ state: "failed", steps, detail: operation.detail })
    } catch (error) {
      emit({ state: "failed", steps, detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}

function sameTargets(
  preview: InstanceDeletePreviewResult,
  command: InstanceDeleteCommand,
): boolean {
  const same = (left: string[], right: string[]) =>
    left.length === right.length && left.every((target, index) => target === right[index])
  return same(preview.directories, command.directories) && same(preview.volumes, command.volumes)
}
