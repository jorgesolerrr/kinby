import { CallError } from "@kinby/contract"
import type { Client, Clock, OperationGetResult, OperationStep } from "@kinby/contract"

/** How often a follower reads an operation that is still running. */
const POLL_MS = 1_000

/** A follower's pause between polls. Stopping it cancels the pause under way for good. */
export interface Pace {
  wait: () => Promise<void>
  stop: () => void
  readonly stopped: boolean
}

export function pace(clock: Clock): Pace {
  let stopped = false
  let cancel = () => {}
  return {
    wait: () => new Promise<void>((resolve) => (cancel = clock.after(POLL_MS, resolve))),
    stop: () => {
      stopped = true
      cancel()
    },
    get stopped() {
      return stopped
    },
  }
}

/** Whether a call failed because the connection dropped, not because the hub refused it. */
export function lost(error: unknown): boolean {
  return error instanceof CallError && error.code === "CONNECTION_LOST"
}

/** Ask again after a dropped connection. Only for a call that is safe to repeat. */
export async function retried<T>(call: () => Promise<T>, pacing: Pace): Promise<T> {
  for (;;) {
    try {
      return await call()
    } catch (error) {
      if (!lost(error)) throw error
      await pacing.wait()
    }
  }
}

/** Poll an operation until it finishes, reporting the steps it has reached while it runs. */
export async function finished(
  caller: Pick<Client, "call">,
  operationId: string,
  pacing: Pace,
  progress: (steps: OperationStep[]) => void,
): Promise<OperationGetResult> {
  for (;;) {
    const operation = await retried(
      () => caller.call("operation.get", { operation_id: operationId }),
      pacing,
    )
    if (operation.state === "succeeded" || operation.state === "failed") return operation
    progress(operation.steps ?? [])
    await pacing.wait()
  }
}

/** What to tell the user about an error nothing else explains. */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
