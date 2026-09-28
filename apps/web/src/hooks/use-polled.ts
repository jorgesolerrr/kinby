import type { Clock } from "@kinby/contract"
import { useCallback, useEffect, useRef, useState } from "react"

/**
 * What `read` last returned. It reads again each time the connection comes back, the window
 * regains focus, `intervalMs` passes while the page is visible, or when asked to. The value shown
 * stays until the next one arrives. The promise settles once that value is stored. `read` must keep
 * its identity across renders, or it starts over.
 */
export function usePolled<T>(
  read: () => Promise<T>,
  clock: Clock,
  connected: boolean,
  intervalMs: number,
): [T | undefined, () => Promise<void>] {
  const [value, setValue] = useState<T>()
  // A newer read, or a drop, retires the one already in flight.
  const generation = useRef(0)
  const connectedRef = useRef(false)
  const readAgain = useCallback(() => {
    if (!connectedRef.current) return Promise.resolve()
    const mine = ++generation.current
    return read().then(
      (next) => {
        if (mine === generation.current) setValue(next)
      },
      // A dropped socket shows as reconnecting, and the value stays as it was until it is back.
      () => {},
    )
  }, [read])
  useEffect(() => {
    connectedRef.current = connected
    if (!connected) {
      generation.current += 1
      return
    }
    void readAgain()
  }, [connected, readAgain])
  // Another tab, the CLI, or the instance itself may change what was read, with no word to this one.
  useEffect(() => {
    let cancel: (() => void) | undefined
    const poll = () => {
      cancel?.()
      cancel =
        document.visibilityState === "visible"
          ? clock.after(intervalMs, () => {
              void readAgain()
              poll()
            })
          : undefined
    }
    const onFocus = () => void readAgain()
    poll()
    document.addEventListener("visibilitychange", poll)
    window.addEventListener("focus", onFocus)
    return () => {
      cancel?.()
      document.removeEventListener("visibilitychange", poll)
      window.removeEventListener("focus", onFocus)
    }
  }, [clock, intervalMs, readAgain])
  return [value, readAgain]
}
