import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  OperationStep,
  PackageDescription,
  PackageSelection,
} from "@kinby/contract"

/** How often the wizard reads a preparation that is still running. */
const POLL_MS = 1_000

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
  let stopped = false
  let cancelWait = () => {}
  const wait = () => new Promise<void>((resolve) => (cancelWait = clock.after(POLL_MS, resolve)))
  const emit = (preparation: Preparation) => {
    if (!stopped) report(preparation)
  }

  // A dropped call is asked again. Preparing a selection already being prepared returns
  // that preparation, and the other two calls only read.
  const retried = async <T>(call: () => Promise<T>): Promise<T> => {
    for (;;) {
      try {
        return await call()
      } catch (error) {
        if (!(error instanceof CallError && error.code === "CONNECTION_LOST")) throw error
        await wait()
      }
    }
  }

  const follow = async () => {
    let steps: OperationStep[] = []
    try {
      const { operation_id } = await retried(() =>
        caller.call("image.prepare", { package: selection }),
      )
      for (;;) {
        const operation = await retried(() => caller.call("operation.get", { operation_id }))
        steps = operation.steps ?? []
        if (operation.state === "failed") {
          return emit({ state: "failed", steps, detail: operation.detail })
        }
        if (operation.state === "succeeded") break
        emit({ state: "preparing", steps })
        await wait()
      }
      const description = await retried(() =>
        caller.call("package.describe", { package: selection }),
      )
      emit({ state: "prepared", steps, description })
    } catch (error) {
      emit({
        state: "failed",
        steps,
        detail: error instanceof Error ? error.message : String(error),
      })
    }
  }

  void follow()
  return () => {
    stopped = true
    cancelWait()
  }
}
