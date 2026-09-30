import type { Clock } from "@kinby/contract"
import { useCallback, useEffect, useState } from "react"

import { usePace } from "@/hooks/use-pace"
import { retried } from "@/lib/operation"

/**
 * What `read` last returned, or why it failed. It reads on open, whenever `read` changes, when the
 * window regains focus, and on `readAgain`, and never polls. `read` must keep its identity across
 * renders, or it reads on every one. A new `read` drops what the last one returned.
 */
export function useRead<T>(
  read: () => Promise<T>,
  clock: Clock,
): { value: T | undefined; failure: unknown; readAgain: () => void } {
  const pacing = usePace(clock)
  const [reads, setReads] = useState(0)
  const [outcome, setOutcome] = useState<Outcome<T>>()
  const readAgain = useCallback(() => setReads((count) => count + 1), [])

  useEffect(() => {
    let current = true
    retried(read, pacing).then(
      (value) => {
        if (current) setOutcome({ read, value })
      },
      (failure: unknown) => {
        if (!current) return
        setOutcome((last) => ({
          read,
          value: last?.read === read ? last.value : undefined,
          failure,
        }))
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

  const mine = outcome?.read === read ? outcome : undefined
  return { value: mine?.value, failure: mine?.failure, readAgain }
}

/** What one `read` returned, or why it failed. A failed read again keeps what it last returned. */
interface Outcome<T> {
  read: () => Promise<T>
  value: T | undefined
  failure?: unknown
}
