import type { Clock, InstanceClient } from "@kinby/contract"
import { useEffect, useState } from "react"

import { pace, retried } from "@/lib/operation"

/**
 * The window of the model behind the thread's latest model call, from the instance's usage read,
 * or none while it is unknown. The read lists a thread once a turn of it completed, and the next
 * turn may run on another model, so it reads again each time `closed`, the count of the thread's
 * closed turns, changes.
 */
export function useModelWindow(
  client: Pick<InstanceClient, "call">,
  clock: Clock,
  threadId: string,
  closed: number,
): number | undefined {
  const [modelWindow, setModelWindow] = useState<number>()

  useEffect(() => {
    const pacing = pace(clock)
    retried(() => client.call("usage.get", {}), pacing).then(
      ({ threads }) => {
        if (pacing.stopped) return
        setModelWindow(threads.find((thread) => thread.thread_id === threadId)?.window ?? undefined)
      },
      // Without a window the bar still shows the context size.
      () => {},
    )
    return pacing.stop
  }, [client, clock, threadId, closed])

  return modelWindow
}
