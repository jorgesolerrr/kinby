import type { Client, InstanceSummary } from "@kinby/contract"
import { CornerDownLeftIcon } from "lucide-react"
import { useState, useSyncExternalStore } from "react"

import { Field, FieldDescription, FieldError } from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
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
import { reason } from "@/lib/operation"
import { selectThread } from "@/lib/selection"

/**
 * Where the app opens: a greeting and a composer that starts a thread on the instance picked in
 * "Send to", then opens it. "Send to" lists the running instances and opens on `to`, the home
 * instance, or the first running one, in that order. Enter sends, and Shift+Enter starts a new line.
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
  const [message, setMessage] = useState("")
  const [sending, setSending] = useState(false)
  const [failure, setFailure] = useState<string>()
  const text = message.trim()

  const send = async () => {
    if (text === "" || sending || !connected || client === undefined || picked === undefined) return
    setSending(true)
    try {
      const created = await client.call("thread.create", {})
      await client.call("thread.turn.start", { thread_id: created.id, message: text })
      selectThread(picked.instance_id, created.id)
    } catch (error) {
      setFailure(reason(error))
      setSending(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 p-6">
      <h1 className="text-center font-heading text-3xl font-semibold">
        {greeting(new Date().getHours())}
      </h1>
      <Field data-invalid={failure !== undefined || undefined}>
        <InputGroup>
          <InputGroupTextarea
            variant="prompt"
            className="max-h-48 min-h-24"
            aria-label="Message"
            placeholder="How can I help?"
            disabled={picked === undefined}
            value={message}
            aria-invalid={failure !== undefined || undefined}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return
              event.preventDefault()
              void send()
            }}
          />
          <InputGroupAddon align="block-end" className="justify-between">
            <Select
              items={running.map((instance) => ({
                value: instance.instance_id,
                label: label(instance),
              }))}
              value={picked?.instance_id ?? null}
              onValueChange={(next) => next !== null && setChoice(next)}
              disabled={picked === undefined}
            >
              <SelectTrigger size="sm" aria-label="Send to">
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
            <InputGroupButton
              size="sm"
              variant="default"
              // aria-disabled, so an empty draft leaves the field looking ready to type in.
              aria-disabled={text === "" || sending || !connected}
              onClick={() => void send()}
            >
              Send
              <CornerDownLeftIcon data-icon="inline-end" />
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
        {picked === undefined && (
          <FieldDescription>
            No instance is running. Start one from its page to send it a message.
          </FieldDescription>
        )}
        {failure !== undefined && <FieldError>{failure}</FieldError>}
      </Field>
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
