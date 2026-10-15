import type { Client, InstanceClient, InstanceSummary } from "@kinby/contract"
import { useEffect, useSyncExternalStore } from "react"

import { useInstanceClient } from "@/hooks/use-instance-client"
import { landOn } from "@/lib/selection"

/**
 * Opens the home instance where no instance is selected: on its newest thread while it runs, or on
 * its page when it is not running, has no thread yet, or cannot list them.
 */
export function OpenHome({
  client,
  home,
}: {
  client: Pick<Client, "instance">
  home: InstanceSummary
}) {
  const running = home.process === "running"
  const instanceClient = useInstanceClient(client, running ? home.instance_id : undefined)
  useEffect(() => {
    if (!running) landOn(home.instance_id)
  }, [running, home.instance_id])
  if (instanceClient === undefined) return null
  return <OpenNewestThread client={instanceClient} instanceId={home.instance_id} />
}

function OpenNewestThread({
  client,
  instanceId,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  instanceId: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  useEffect(() => {
    if (!connected) return
    let current = true
    client.call("thread.list", { filter: "sidebar", limit: 1 }).then(
      ({ threads: [newest] }) => current && landOn(instanceId, newest?.id),
      () => current && landOn(instanceId),
    )
    return () => {
      current = false
    }
  }, [client, connected, instanceId])
  return null
}
