import type { Client, Clock, LoginPrompt, OperationStep } from "@kinby/contract"

import { finished, pace, reason, retried } from "@/lib/operation"

export type SignIn =
  | { state: "signing-in"; prompt: LoginPrompt | null }
  | { state: "signed-in" }
  | { state: "failed"; detail: string }

/** The URL and code the step waiting for the user shows, once the hub found them. */
function promptOf(steps: OperationStep[]): LoginPrompt | null {
  return steps.find((step) => step.state === "running")?.prompt ?? null
}

/**
 * Start one of an instance's subscription logins and report its URL and code until it ends.
 * Starting a login that is running returns it, so a dropped call is asked again, and a reload
 * shows the same code. The returned function stops following it; the hub carries on.
 */
export function followLogin(
  caller: Pick<Client, "call">,
  instanceId: string,
  loginId: string,
  report: (signIn: SignIn) => void,
  clock: Clock,
): () => void {
  const pacing = pace(clock)
  const emit = (signIn: SignIn) => {
    if (!pacing.stopped) report(signIn)
  }

  const follow = async () => {
    try {
      const { operation_id } = await retried(
        () => caller.call("instance.login.start", { instance_id: instanceId, login_id: loginId }),
        pacing,
      )
      const operation = await finished(caller, operation_id, pacing, (steps) =>
        emit({ state: "signing-in", prompt: promptOf(steps) }),
      )
      if (operation.state === "failed") return emit({ state: "failed", detail: operation.detail })
      emit({ state: "signed-in" })
    } catch (error) {
      emit({ state: "failed", detail: reason(error) })
    }
  }

  void follow()
  return pacing.stop
}
