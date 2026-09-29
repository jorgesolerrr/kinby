import type { Clock } from "@kinby/contract"
import { useEffect, useState } from "react"

import { type Pace, pace } from "@/lib/operation"

/** A pace for the component's reads to ask again at, stopped when the component unmounts. */
export function usePace(clock: Clock): Pace {
  const [pacing] = useState(() => pace(clock))
  useEffect(() => pacing.stop, [pacing])
  return pacing
}
