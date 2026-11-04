import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { useCallback, useState } from "react"

import { useFollowing } from "@/hooks/use-following"
import { followStart, type Starting } from "@/lib/creation"

/** The start of an instance asked here, and how it goes. */
export interface Start {
  /** Undefined until the hub reports on a start asked here. */
  starting: Starting | undefined
  /** A start asked here runs. */
  busy: boolean
  ask: () => void
}

/**
 * Starts the instance, and `onStarted`, which must keep its identity, hears it run. A start asked
 * for another instance is forgotten once `instance` names a new one.
 */
export function useStart(
  caller: Pick<Client, "call">,
  clock: Clock,
  instance: InstanceSummary,
  onStarted: () => void,
): Start {
  const [request, setRequest] = useState<{ instanceId: string }>()
  const follow = useCallback(
    (asked: { instanceId: string }, report: (starting: Starting) => void) =>
      followStart(
        caller,
        asked.instanceId,
        (starting) => {
          report(starting)
          if (starting.state === "started") onStarted()
        },
        clock,
      ),
    [caller, clock, onStarted],
  )
  const starting = useFollowing(request, follow)
  // A start ends once the instances are listed again with the instance meant to run, so Start
  // offered after a later stop is not busy.
  if (
    request !== undefined &&
    (request.instanceId !== instance.instance_id ||
      (starting?.state === "started" && instance.intended_state === "running"))
  ) {
    setRequest(undefined)
  }
  return {
    starting,
    busy: request !== undefined && starting?.state !== "failed",
    ask: () => setRequest({ instanceId: instance.instance_id }),
  }
}
