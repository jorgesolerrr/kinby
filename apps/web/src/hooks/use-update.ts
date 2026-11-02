import type { Client, Clock } from "@kinby/contract"
import { useCallback, useState } from "react"

import { useFollowing } from "@/hooks/use-following"
import { followUpdate, type Updating } from "@/lib/update"

/** The update of an instance's core asked here, and how it goes. */
export interface Update {
  /** Undefined until the hub reports on an update asked here. */
  updating: Updating | undefined
  /** An update asked here runs. */
  busy: boolean
  /** Update the core to `revision`: a tag, a branch, or a commit in the hub's checkout. */
  ask: (revision: string) => void
}

/** Updates the instance's core, and `onUpdated`, which must keep its identity, hears it succeed. */
export function useUpdate(
  caller: Pick<Client, "call">,
  clock: Clock,
  instanceId: string,
  onUpdated: () => void,
): Update {
  const [request, setRequest] = useState<{ revision: string }>()
  const follow = useCallback(
    ({ revision }: { revision: string }, report: (updating: Updating) => void) =>
      followUpdate(
        caller,
        { instance_id: instanceId, revision },
        (updating) => {
          report(updating)
          if (updating.state === "updated") onUpdated()
        },
        clock,
      ),
    [caller, clock, instanceId, onUpdated],
  )
  const updating = useFollowing(request, follow)
  return {
    updating,
    busy: request !== undefined && (updating === undefined || updating.state === "updating"),
    ask: (revision) => setRequest({ revision }),
  }
}
