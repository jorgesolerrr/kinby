import type { Client, InstanceClient } from "@kinby/contract"
import { useCallback } from "react"

import { useFollowing } from "@/hooks/use-following"

/** One instance's own connection, open while `instanceId` names it and closed after. */
export function useInstanceClient(
  client: Pick<Client, "instance">,
  instanceId: string | undefined,
): InstanceClient | undefined {
  const open = useCallback(
    (instanceId: string, report: (opened: InstanceClient) => void) => {
      const opened = client.instance(instanceId)
      report(opened)
      return () => opened.close()
    },
    [client],
  )
  return useFollowing(instanceId, open)
}
