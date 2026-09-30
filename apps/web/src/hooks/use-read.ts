import type { Clock } from "@kinby/contract"
import { useCallback, useEffect, useState } from "react"

import { usePace } from "@/hooks/use-pace"
import { retried } from "@/lib/operation"

/**
 * What `read` last returned, or why it failed. It reads on open, whenever `read` changes, when the
 * window regains focus, and on `readAgain`, and never polls. `read` must keep its identity across
 * renders, or it reads on every one.
 */
export function useRead<T>(
  read: () => Promise<T>,
  clock: Clock,
): { value: T | undefined; failure: unknown; readAgain: () => void } {
  const pacing = usePace(clock)
  const [reads, setReads] = useState(0)
  const [value, setValue] = useState<T>()
  const [failure, setFailure] = useState<unknown>()
  const readAgain = useCallback(() => setReads((count) => count + 1), [])

  useEffect(() => {
    let current = true
    retried(read, pacing).then(
      (next) => {
        if (!current) return
        setValue(next)
        setFailure(undefined)
      },
      (error: unknown) => {
        if (current) setFailure(error)
      },
    )
    return () => {
      current = false
    }
  }, [read, pacing, reads])
  // What was read changes while the user is elsewhere, and nothing tells the page.
  useEffect(() => {
    window.addEventListener("focus", readAgain)
    return () => window.removeEventListener("focus", readAgain)
  }, [readAgain])

  return { value, failure, readAgain }
}
