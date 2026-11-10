import type { Clock, InstanceClient } from "@kinby/contract"
import { useEffect, useState } from "react"

import { pace, retried } from "@/lib/operation"

/**
 * The window of `model`, the model of the thread's latest model call, from the instance's usage
 * read, or none while it is unknown. Each read scans the instance's whole history, so none runs
 * while `closed`, the count of the thread's closed turns, is unknown during the replay. The read
 * lists a thread once a turn of it completed, so it reads again each time `closed` changes, and
 * when a call runs on another model.
 */
export function useModelWindow(
  client: Pick<InstanceClient, "call">,
  clock: Clock,
  threadId: string,
  model: string | undefined,
  closed: number | undefined,
): number | undefined {
  const [read, setRead] = useState<{ model: string; window: number | undefined }>()

  useEffect(() => {
    if (model === undefined || closed === undefined) return
    const pacing = pace(clock)
    retried(() => client.call("usage.get", {}), pacing).then(
      ({ threads }) => {
        if (pacing.stopped) return
        const thread = threads.find((each) => each.thread_id === threadId)
        setRead({ model, window: thread?.window ?? undefined })
      },
      // Without a window the bar still shows the context size.
      () => {},
    )
    return pacing.stop
  }, [client, clock, threadId, model, closed])

  return read !== undefined && read.model === model ? read.window : undefined
}
