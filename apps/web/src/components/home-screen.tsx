import type { Client, Clock, InstanceSetup, InstanceSummary, SecretSetup } from "@kinby/contract"
import { CirclePauseIcon, CircleXIcon, KeyRoundIcon, PlayIcon } from "lucide-react"
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

import { Composer } from "@/components/composer"
import { InstanceAvatar } from "@/components/instance-avatar"
import { Secrets } from "@/components/secrets-section"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { useRead } from "@/hooks/use-read"
import { useSidebarThreads } from "@/hooks/use-sidebar-threads"
import { type Start, useStart } from "@/hooks/use-start"
import { instanceName } from "@/lib/instances"
import { selectInstance, selectThread } from "@/lib/selection"

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
 * start says why in the alert and keeps the message. An instance whose status says a required
 * secret is missing shows a card over a disabled composer instead, asking for each such secret.
 * For the home instance that is its first run, and the screen welcomes the user. Otherwise a
 * running instance whose sidebar set of threads is empty is greeted by its avatar, with the skills
 * it holds. `onChanged` lists the instances again once a start or a secret succeeds, and must keep
 * its identity.
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
  const { setup, onSet } = useSetup(caller, clock, picked.instance_id, onChanged)
  const missing = setup?.secrets.filter((secret) => secret.required && !secret.is_set) ?? []
  const needsSetup = setup !== undefined && missing.length > 0
  const start = useStart(caller, clock, picked, onChanged)
  const queued = useRef<Queued>(undefined)
  const client = useInstanceClient(caller, stopped ? undefined : picked.instance_id)
  const connected =
    useSyncExternalStore(client?.onStateChange ?? unsubscribed, () => client?.state()) ===
    "connected"
  const threads = useSidebarThreads(client, clock)
  const noThreads = !needsSetup && threads?.length === 0
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
      {needsSetup && picked.home ? (
        <div className="flex flex-col gap-2 text-center">
          <h1 className="font-heading text-3xl font-semibold">Welcome to kinby</h1>
          <p className="text-sm text-muted-foreground">
            {instanceName(picked)} is your home instance. It does personal work for you and builds
            the rest of kinby on your behalf.
          </p>
        </div>
      ) : noThreads ? (
        <Ready caller={caller} clock={clock} instance={picked} />
      ) : (
        <h1 className="text-center font-heading text-3xl font-semibold">
          {greeting(new Date().getHours())}
        </h1>
      )}
      <div className="flex flex-col gap-2">
        {needsSetup ? (
          <SetupCard
            caller={caller}
            clock={clock}
            instance={picked}
            setup={setup}
            missing={missing}
            onSet={onSet}
          />
        ) : (
          stopped && <StoppedAlert name={instanceName(picked)} start={start} />
        )}
        <Composer
          label="Message"
          placeholder={
            needsSetup ? `Add ${instanceName(picked)}'s key to start` : "How can I help?"
          }
          name={instanceName(picked)}
          disabled={needsSetup}
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

/**
 * The instance's setup, as its status has it, undefined until read. `onSet` reads it again and
 * calls `onChanged`, and keeps its identity while `onChanged` does.
 */
function useSetup(
  caller: Pick<Client, "call">,
  clock: Clock,
  instanceId: string,
  onChanged: () => void,
): { setup: InstanceSetup | undefined; onSet: () => void } {
  const read = useCallback(
    () => caller.call("instance.status", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const { value, readAgain } = useRead(read, clock)
  const onSet = useCallback(() => {
    readAgain()
    onChanged()
  }, [readAgain, onChanged])
  return { setup: value?.setup, onSet }
}

/** Greets an instance with no threads by its avatar and its name, and names the skills it holds. */
function Ready({
  caller,
  clock,
  instance,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
}) {
  const read = useCallback(
    () => caller.call("instance.picks.get", { instance_id: instance.instance_id }),
    [caller, instance.instance_id],
  )
  const skills = useRead(read, clock).value?.picks.map((pick) => pick.name)
  const name = instanceName(instance)
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <InstanceAvatar avatar={instance.avatar} name={name} size="lg" />
      <h1 className="font-heading text-3xl font-semibold">{name} is ready</h1>
      <p className="text-sm text-muted-foreground">
        No threads yet.
        {skills !== undefined &&
          (skills.length === 0 ? (
            " It holds no skills."
          ) : (
            <>
              {` It holds ${skills.length} ${skills.length === 1 ? "skill" : "skills"}: `}
              <span className="font-mono text-foreground">{skills.join(", ")}</span>.
            </>
          ))}
      </p>
    </div>
  )
}

/**
 * Asks for each required secret the instance lacks, as its page's setup card does, and links to
 * its page while a login or a list of repository commands is pending there too.
 */
function SetupCard({
  caller,
  clock,
  instance,
  setup,
  missing,
  onSet,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  setup: InstanceSetup
  missing: SecretSetup[]
  onSet: () => void
}) {
  const titleId = useId()
  const name = instanceName(instance)
  const elsewhere =
    setup.logins.some((login) => login.state !== "signed_in") || setup.missing_commands.length > 0
  return (
    <section aria-labelledby={titleId}>
      <Card size="sm" variant="attention">
        <CardHeader>
          <CardTitle id={titleId}>
            <span className="flex items-center gap-2">
              <KeyRoundIcon aria-hidden className="size-4 shrink-0 text-ember" />
              {name} needs setup to answer
            </span>
          </CardTitle>
          <CardDescription>
            Set what it needs. The hub keeps each secret and never shows it back.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Secrets
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            secrets={missing}
            onSet={onSet}
          />
        </CardContent>
        {elsewhere && (
          <CardFooter>
            <div className="flex w-full flex-wrap items-center justify-between gap-2">
              <p className="text-muted-foreground">
                Finish its sign-ins and repository commands on its page.
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => selectInstance(instance.instance_id)}
              >
                Open {name}
              </Button>
            </div>
          </CardFooter>
        )}
      </Card>
    </section>
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
