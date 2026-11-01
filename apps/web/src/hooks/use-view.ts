import { CallError, type InstanceClient, type View } from "@kinby/contract"
import { useCallback } from "react"

import { useFollowing } from "@/hooks/use-following"

/** Where following a view stands: its latest snapshot, why following it failed, or that it is gone. */
export interface FollowedView {
  view?: View
  failure?: unknown
  deleted?: boolean
}

/**
 * Follow one of an instance's views while `viewId` names it, with each snapshot the instance sends
 * after a write. A failure keeps the snapshot shown. A view is gone when its subscription ends or
 * the instance does not find it.
 */
export function useView(
  client: Pick<InstanceClient, "subscribe">,
  viewId: string | undefined,
): FollowedView {
  const follow = useCallback(
    (id: string, report: (followed: FollowedView) => void) => {
      const snapshots = client.subscribe("view.subscribe", { id })
      let following = true
      let last: View | undefined
      const read = async () => {
        try {
          for await (const snapshot of snapshots) {
            last = snapshot
            if (following) report({ view: snapshot })
          }
          if (following) report({ deleted: true })
        } catch (error) {
          if (!following) return
          if (error instanceof CallError && error.code === "NOT_FOUND") report({ deleted: true })
          else report({ view: last, failure: error })
        }
      }
      void read()
      return () => {
        following = false
        snapshots.cancel()
      }
    },
    [client],
  )
  return useFollowing(viewId, follow) ?? {}
}
