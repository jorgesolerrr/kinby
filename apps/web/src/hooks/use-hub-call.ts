import { CallError } from "@kinby/contract"
import { useState } from "react"

/** Make one call to the hub at a time, keeping why the hub refused it or how it failed. */
export function useHubCall() {
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()

  const reset = () => {
    setRefused({})
    setFailure(undefined)
  }
  const ask = async (call: () => Promise<void>) => {
    setBusy(true)
    reset()
    try {
      await call()
    } catch (error) {
      if (error instanceof CallError && Object.keys(error.fields).length > 0) {
        setRefused(error.fields)
      } else setFailure(error)
    } finally {
      setBusy(false)
    }
  }
  const reasons = Object.values(refused)
  const refusal = reasons.length > 0 ? reasons.join(" ") : undefined
  return { busy, refusal, refused, failure, ask, reset }
}
