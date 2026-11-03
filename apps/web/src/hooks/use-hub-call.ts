import { CallError } from "@kinby/contract"
import { useState } from "react"

/** Make one call to the hub at a time, keeping why the hub refused it or how it failed. */
export function useHubCall() {
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string>()
  const [failure, setFailure] = useState<unknown>()

  const ask = async (call: () => Promise<void>) => {
    setBusy(true)
    setRefusal(undefined)
    setFailure(undefined)
    try {
      await call()
    } catch (error) {
      if (error instanceof CallError && Object.keys(error.fields).length > 0) {
        setRefusal(Object.values(error.fields).join(" "))
      } else setFailure(error)
    } finally {
      setBusy(false)
    }
  }
  return { busy, refusal, failure, ask }
}
