import type {
  Client,
  Clock,
  OperationStep,
  PackageDescription,
  PackageSelection,
} from "@kinby/contract"

import { finished, pace, reason, retried } from "@/lib/operation"

export type Preparation =
  | { state: "preparing"; steps: OperationStep[] }
  | { state: "failed"; steps: OperationStep[]; detail: string }
  | { state: "prepared"; steps: OperationStep[]; description: PackageDescription }

/**
 * Prepare a selection's image, report each step the hub reaches, then what the image declares.
 * Vanilla is the null selection. The returned function stops following it; the hub carries on.
 */
export function followPreparation(
  caller: Pick<Client, "call">,
  selection: PackageSelection | null,
  report: (preparation: Preparation) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (preparation: Preparation) => {
    if (!pacing.stopped) report(preparation)
  }

  // A dropped call is asked again. Preparing a selection already being prepared returns
  // that preparation, and the other two calls only read.
  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const { operation_id } = await retried(
        () => caller.call("image.prepare", { package: selection }),
        pacing,
      )
      const operation = await finished(caller, operation_id, pacing, (reached) => {
        steps = reached
        emit({ state: "preparing", steps })
      })
      steps = operation.steps ?? []
      if (operation.state === "failed") {
        return emit({ state: "failed", steps, detail: operation.detail })
      }
      const description = await retried(
        () => caller.call("package.describe", { package: selection }),
        pacing,
      )
      emit({ state: "prepared", steps, description })
    } catch (error) {
      emit({ state: "failed", steps, detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}
