import type { Client, Clock, StatsGetResult, StatsRunsResult } from "@kinby/contract"
import { useEffect, useState } from "react"

import { pace, retried } from "@/lib/operation"
import { type Range, statsCommand } from "@/lib/stats"

export interface RunUsageRead {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  range: Range
  /** The stats read the run usage goes with. Each new one reads the run usage again. */
  stats: StatsGetResult
}

/** The hub's run usage of the factory runs the instance handed in during the range. */
export function useRunUsage({ caller, clock, instanceId, range, stats }: RunUsageRead): {
  usage?: StatsRunsResult
  failure?: unknown
} {
  const [usage, setUsage] = useState<StatsRunsResult>()
  const [failure, setFailure] = useState<unknown>()

  useEffect(() => {
    const pacing = pace(clock)
    const { since } = statsCommand(range, new Date())
    retried(() => caller.call("stats.runs", { instance_id: instanceId, since }), pacing).then(
      (read) => {
        if (pacing.stopped) return
        setUsage(read)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (!pacing.stopped) setFailure(error)
      },
    )
    return pacing.stop
  }, [caller, clock, instanceId, range, stats])

  return { usage, failure }
}
