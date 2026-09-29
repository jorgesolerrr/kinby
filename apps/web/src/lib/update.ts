import type { Client, Clock, InstanceUpdateCommand, OperationStep } from "@kinby/contract"

import { finished, pace, reason } from "@/lib/operation"

/**
 * How an update of an instance's core stands. `blocked` stopped before the replacement began, so
 * the instance runs on as it was, and `problems` lists what stopped it, one per line of the hub's
 * detail.
 */
export type Updating =
  | { state: "updating"; steps: OperationStep[] }
  | { state: "blocked"; steps: OperationStep[]; problems: string[] }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "updated"; steps: OperationStep[] }

/** The step where the hub starts replacing the container. Nothing before it touches the instance. */
const REPLACE_STEP = "replace"

/**
 * Update an instance and report each step the hub reaches. The returned function stops following
 * it; the hub carries on.
 */
export function followUpdate(
  caller: Pick<Client, "call">,
  command: InstanceUpdateCommand,
  report: (updating: Updating) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (updating: Updating) => {
    if (!pacing.stopped) report(updating)
  }

  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const accepted = await caller.call("instance.update", command)
      const operation = await finished(caller, accepted.operation_id, pacing, (reached) => {
        steps = reached
        emit({ state: "updating", steps })
      })
      steps = operation.steps ?? []
      if (operation.state === "succeeded") return emit({ state: "updated", steps })
      if (!steps.some((step) => step.name === REPLACE_STEP)) {
        return emit({ state: "blocked", steps, problems: lines(operation.detail) })
      }
      emit({ state: "failed", steps, detail: operation.detail })
    } catch (error) {
      emit({ state: "failed", steps, detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
}
