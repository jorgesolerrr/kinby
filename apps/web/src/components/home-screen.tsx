import type { Client, InstanceSummary } from "@kinby/contract"
import { useState, useSyncExternalStore } from "react"

import { Composer } from "@/components/composer"
import { FieldDescription } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { instanceName } from "@/lib/instances"
import { selectThread } from "@/lib/selection"

/**
 * Where the app opens: a greeting and a composer that starts a thread on the instance picked in
 * "Send to", then opens it. "Send to" lists the running instances and opens on `to`, the home
 * instance, or the first running one, in that order.
 */
export function HomeScreen({
  caller,
  instances,
  to,
}: {
  caller: Pick<Client, "instance">
  instances: InstanceSummary[]
  to: string | undefined
}) {
  const running = instances.filter((instance) => instance.process === "running")
  const [choice, setChoice] = useState(to)
  const picked =
    running.find((instance) => instance.instance_id === choice) ??
    running.find((instance) => instance.home) ??
    running[0]
  const client = useInstanceClient(caller, picked?.instance_id)
  const connected =
    useSyncExternalStore(client?.onStateChange ?? unsubscribed, () => client?.state()) ===
    "connected"
  const send = async (message: string) => {
    if (client === undefined || picked === undefined) return
    const created = await client.call("thread.create", {})
    await client.call("thread.turn.start", { thread_id: created.id, message })
    selectThread(picked.instance_id, created.id)
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 p-6">
      <h1 className="text-center font-heading text-3xl font-semibold">
        {greeting(new Date().getHours())}
      </h1>
      <div className="flex flex-col gap-2">
        <Composer
          label="Message"
          placeholder="How can I help?"
          name={picked && instanceName(picked)}
          disabled={picked === undefined}
          connected={connected}
          onSend={send}
        >
          <Select
            items={running.map((instance) => ({
              value: instance.instance_id,
              label: label(instance),
            }))}
            value={picked?.instance_id ?? null}
            onValueChange={(next) => next !== null && setChoice(next)}
            disabled={picked === undefined}
          >
            <SelectTrigger size="sm" aria-label="Send to" className="min-w-0">
              <SelectValue placeholder="No instance" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {running.map((instance) => (
                  <SelectItem key={instance.instance_id} value={instance.instance_id}>
                    {label(instance)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Composer>
        {picked === undefined && (
          <FieldDescription>
            No instance is running. Start one from its page to send it a message.
          </FieldDescription>
        )}
      </div>
    </div>
  )
}

function unsubscribed(): () => void {
  return () => {}
}

function label(instance: InstanceSummary): string {
  return instance.home ? `${instanceName(instance)} (home)` : instanceName(instance)
}

function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning"
  if (hour >= 12 && hour < 18) return "Good afternoon"
  return "Good evening"
}
