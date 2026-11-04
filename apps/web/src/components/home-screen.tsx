import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { CirclePauseIcon, CircleXIcon, PlayIcon } from "lucide-react"
import { useEffect, useEffectEvent, useRef, useState, useSyncExternalStore } from "react"

import { Composer } from "@/components/composer"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { type Start, useStart } from "@/hooks/use-start"
import { instanceName } from "@/lib/instances"
import { selectThread } from "@/lib/selection"

/** A message written to a stopped instance, sent once it runs, and kept if another is picked. */
interface Queued {
  instanceId: string
  message: string
  /** Ends the composer's send: `false` keeps the message, and a send clears it once it succeeds. */
  settle: (sent: false | Promise<unknown>) => void
}

/**
 * Where the app opens: a greeting and a composer that starts a thread on the instance picked in
 * "Send to", then opens it. "Send to" lists every instance, the running ones first with the home
 * instance first among them, and opens on `to` or the first listed. `instances` holds at least one.
 * A stopped instance picked shows an alert over the composer with Start, and Send becomes Start
 * and send: it starts the instance as the instance header does, and sends once it runs. A failed
 * start says why in the alert and keeps the message. `onChanged` lists the instances again once
 * the start succeeds, and must keep its identity.
 */
export function HomeScreen({
  caller,
  clock,
  instances,
  to,
  onChanged,
}: {
  caller: Pick<Client, "call" | "instance">
  clock: Clock
  instances: InstanceSummary[]
  to: string | undefined
  onChanged: () => void
}) {
  const running = instances.filter((instance) => instance.process === "running")
  const listed = [
    ...running.filter((instance) => instance.home),
    ...running.filter((instance) => !instance.home),
    ...instances.filter((instance) => instance.process !== "running"),
  ]
  const [choice, setChoice] = useState(to)
  const picked = listed.find((instance) => instance.instance_id === choice) ?? listed[0]
  const stopped = picked.process !== "running"
  const start = useStart(caller, clock, picked, onChanged)
  const queued = useRef<Queued>(undefined)
  const client = useInstanceClient(caller, stopped ? undefined : picked.instance_id)
  const connected =
    useSyncExternalStore(client?.onStateChange ?? unsubscribed, () => client?.state()) ===
    "connected"
  const send = async (message: string) => {
    if (client === undefined) return
    const created = await client.call("thread.create", {})
    await client.call("thread.turn.start", { thread_id: created.id, message })
    selectThread(picked.instance_id, created.id)
  }
  const failed = start.starting?.state === "failed"
  const settleQueued = useEffectEvent(() => {
    const waiting = queued.current
    if (waiting === undefined) return
    const elsewhere = waiting.instanceId !== picked.instance_id
    if (!(failed || connected || elsewhere)) return
    queued.current = undefined
    waiting.settle(failed || elsewhere ? false : send(waiting.message))
  })
  useEffect(() => settleQueued(), [failed, connected, picked.instance_id])
  const startAndSend = (message: string) =>
    new Promise<unknown>((settle) => {
      queued.current = { instanceId: picked.instance_id, message, settle }
      start.ask()
    })

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 p-6">
      <h1 className="text-center font-heading text-3xl font-semibold">
        {greeting(new Date().getHours())}
      </h1>
      <div className="flex flex-col gap-2">
        {stopped && <StoppedAlert name={instanceName(picked)} start={start} />}
        <Composer
          label="Message"
          placeholder="How can I help?"
          name={instanceName(picked)}
          connected={stopped || connected}
          sendLabel={stopped ? "Start and send" : "Send"}
          onSend={stopped ? startAndSend : send}
        >
          <Select
            items={listed.map((instance) => ({
              value: instance.instance_id,
              label: label(instance),
            }))}
            value={picked.instance_id}
            onValueChange={(next) => next !== null && setChoice(next)}
            disabled={start.busy}
          >
            <SelectTrigger size="sm" aria-label="Send to" className="min-w-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {listed.map((instance) => (
                  <SelectItem key={instance.instance_id} value={instance.instance_id}>
                    {label(instance)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Composer>
      </div>
    </div>
  )
}

/** Says the picked instance is stopped and that sending starts it, with Start, or why it did not. */
function StoppedAlert({ name, start }: { name: string; start: Start }) {
  const failure = start.starting?.state === "failed" ? start.starting.detail : undefined
  return (
    <Alert variant={failure === undefined ? "default" : "destructive"}>
      {failure === undefined ? <CirclePauseIcon /> : <CircleXIcon />}
      <AlertTitle>
        {start.busy
          ? `${name} is starting`
          : failure === undefined
            ? `${name} is stopped`
            : `${name} did not start`}
      </AlertTitle>
      <AlertDescription>{failure ?? "Write anyway: sending starts it first."}</AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline" disabled={start.busy} onClick={start.ask}>
          {start.busy ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <PlayIcon data-icon="inline-start" />
          )}
          Start
        </Button>
      </AlertAction>
    </Alert>
  )
}

function unsubscribed(): () => void {
  return () => {}
}

function label(instance: InstanceSummary): string {
  const marks = [instance.home && "home", instance.process !== "running" && "stopped"].filter(
    Boolean,
  )
  return marks.length === 0
    ? instanceName(instance)
    : `${instanceName(instance)} (${marks.join(", ")})`
}

function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning"
  if (hour >= 12 && hour < 18) return "Good afternoon"
  return "Good evening"
}
