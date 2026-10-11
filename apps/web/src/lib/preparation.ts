import type { Client, Clock, DeclaredSetup, OperationStep } from "@kinby/contract"

import { finished, pace, reason, retried } from "@/lib/operation"

export type Preparation =
  | { state: "preparing"; steps: OperationStep[] }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "prepared"; steps: OperationStep[]; description: DeclaredSetup }

/**
 * Prepare the image, report each step the hub reaches, then what the image declares.
 * The returned function stops following it; the hub carries on.
 */
export function followPreparation(
  caller: Pick<Client, "call">,
  report: (preparation: Preparation) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (preparation: Preparation) => {
    if (!pacing.stopped) report(preparation)
  }

  // A dropped call is asked again. Preparing while a preparation runs returns that
  // preparation, and the other two calls only read.
  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const { operation_id } = await retried(() => caller.call("image.prepare", {}), pacing)
      const operation = await finished(caller, operation_id, pacing, (reached) => {
        steps = reached
        emit({ state: "preparing", steps })
      })
      steps = operation.steps ?? []
      if (operation.state === "failed") {
        return emit({ state: "failed", steps, detail: operation.detail })
      }
      const description = await retried(() => caller.call("image.describe", {}), pacing)
      emit({ state: "prepared", steps, description })
    } catch (error) {
      emit({ state: "failed", steps, detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}
