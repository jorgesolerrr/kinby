import type { Clock } from "@kinby/contract"
import { useEffect, useRef, useState } from "react"

import { type Pace, pace } from "@/lib/operation"

/**
 * A pace for the component's handlers and children to ask again at, stopped when the component
 * unmounts. A remount, as StrictMode simulates, asks again at a fresh one.
 */
export function usePace(clock: Clock): Pace {
  const live = useRef(pace(clock))
  useEffect(() => {
    if (live.current.stopped) live.current = pace(clock)
    return live.current.stop
  }, [clock])
  const [pacing] = useState<Pace>(() => ({
    wait: () => live.current.wait(),
    stop: () => live.current.stop(),
    get stopped() {
      return live.current.stopped
    },
  }))
  return pacing
}
