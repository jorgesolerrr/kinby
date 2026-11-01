import { CallError } from "@kinby/contract"
import type { Channel, ChannelKindDescription, Client, Clock, SetupValue } from "@kinby/contract"
import { BellOffIcon, CircleXIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { SectionFrame } from "@/components/section-frame"
import { SetupInput } from "@/components/onboarding"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { useRead } from "@/hooks/use-read"
import { type FieldInput, initialInput, setupValue } from "@/lib/creation"
import { instanceName } from "@/lib/instances"
import { reason } from "@/lib/operation"

type Caller = Pick<Client, "call">

/** How often the page reads the channels again while one waits for its pairing code. */
const PAIRING_READ_MS = 5_000

/**
 * The hub's channels, each with its pairing, deliveries and mutes, under a form that adds one of
 * the kinds the hub found. No secret setting is ever read back.
 */
export function ChannelsSection({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(async () => {
    const [{ kinds }, { channels }, { instances }] = await Promise.all([
      client.call("channel.kinds", {}),
      client.call("channel.list", {}),
      client.call("instance.list", {}),
    ])
    const names = new Map(
      instances.map((instance) => [instance.instance_id, instanceName(instance)]),
    )
    return { kinds, channels, names }
  }, [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  // The user pairs a channel through its service, and nothing tells the page.
  const pairing = listed?.channels.some((channel) => channel.pairing_code != null) ?? false
  useEffect(
    () => (pairing ? clock.after(PAIRING_READ_MS, readAgain) : undefined),
    [pairing, listed, clock, readAgain],
  )

  return (
    <SectionFrame
      title="Channels"
      lead="Where the hub sends notifications. Every enabled channel gets each one."
    >
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : (
        <>
          <AddChannel caller={client} kinds={listed.kinds} onAdded={readAgain} />
          {listed.channels.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No channels yet</EmptyTitle>
                <EmptyDescription>Add one above to hear from kinby.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <ul aria-label="Channels" className="flex max-w-2xl flex-col gap-4">
              {listed.channels.map((channel) => (
                <ChannelCard
                  key={channel.channel_id}
                  caller={client}
                  channel={channel}
                  kind={listed.kinds.find((kind) => kind.kind === channel.kind)}
                  names={listed.names}
                  onChanged={readAgain}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </SectionFrame>
  )
}

/** The kind's settings and secrets from what the user typed. A blank secret is not sent. */
function createdSettings(kind: ChannelKindDescription, values: Record<string, FieldInput>) {
  const settings: Record<string, SetupValue> = {}
  const secrets: Record<string, string> = {}
  for (const field of kind.settings) {
    const input = values[field.name] ?? initialInput(field)
    if (input === undefined) continue
    if (field.kind === "config") settings[field.name] = setupValue(field, input)
    else if (String(input).trim() !== "") secrets[field.name] = String(input)
  }
  return { settings, secrets }
}

/** A new channel: its kind, its name, and the fields its kind describes. */
function AddChannel({
  caller,
  kinds,
  onAdded,
}: {
  caller: Caller
  kinds: ChannelKindDescription[]
  onAdded: () => void
}) {
  const id = useId()
  const [kind, setKind] = useState<ChannelKindDescription>()
  const [name, setName] = useState("")
  const [values, setValues] = useState<Record<string, FieldInput>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()
  const [adding, setAdding] = useState(false)

  const pick = (picked: string) => {
    setKind(kinds.find((each) => each.kind === picked))
    setValues({})
    setErrors({})
  }

  const add = async (event: FormEvent) => {
    event.preventDefault()
    if (kind === undefined) return
    setAdding(true)
    setErrors({})
    setFailure(undefined)
    try {
      await caller.call("channel.create", {
        kind: kind.kind,
        name,
        ...createdSettings(kind, values),
      })
      setKind(undefined)
      setName("")
      setValues({})
      onAdded()
    } catch (error) {
      if (error instanceof CallError && error.code === "INVALID_SETUP") setErrors(error.fields)
      else setFailure(error)
    } finally {
      setAdding(false)
    }
  }

  // A refusal of no field shown here, such as a kind the hub no longer has.
  const others = Object.entries(errors)
    .filter(([field]) => !kind?.settings.some((each) => each.name === field))
    .map(([, message]) => message)
  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>Add a channel</CardTitle>
        <CardDescription>
          The hub keeps its secret settings as hub secrets and never shows them again.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form aria-label="Add a channel" onSubmit={(event) => void add(event)}>
          <FieldGroup>
            {(failure !== undefined || others.length > 0) && (
              <Alert variant="destructive">
                <CircleXIcon />
                <AlertTitle>The hub refused it</AlertTitle>
                <AlertDescription>
                  {failure !== undefined ? reason(failure) : others.join(" ")}
                </AlertDescription>
              </Alert>
            )}
            <Field>
              <FieldLabel htmlFor={`${id}-kind`}>Kind</FieldLabel>
              <Select
                value={kind?.kind ?? null}
                onValueChange={(picked) => typeof picked === "string" && pick(picked)}
              >
                <SelectTrigger id={`${id}-kind`}>
                  <SelectValue placeholder="Pick one" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {kinds.map((each) => (
                      <SelectItem key={each.kind} value={each.kind}>
                        {each.kind}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
              <Input
                id={`${id}-name`}
                autoComplete="off"
                value={name}
                readOnly={adding}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            {kind?.settings.map((field) => (
              <SetupInput
                key={field.name}
                field={field}
                value={values[field.name] ?? initialInput(field)}
                error={errors[field.name]}
                onChange={(value) => setValues((typed) => ({ ...typed, [field.name]: value }))}
              />
            ))}
            {kind?.pairs && (
              <FieldDescription>
                Once it is added, send it the code the page shows to pair it with you.
              </FieldDescription>
            )}
            <div>
              <Button type="submit" disabled={adding || kind === undefined || name.trim() === ""}>
                {adding ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <PlusIcon data-icon="inline-start" />
                )}
                Add channel
              </Button>
            </div>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  )
}

/** When something happened, in the reader's own time zone. */
function shownAt(instant: string): string {
  return new Date(instant).toLocaleString("en", {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  })
}

/**
 * One channel: enable, rename and delete in its header, then its pairing, the interactions URL a
 * Discord channel needs, its deliveries and mutes, and Send test.
 */
function ChannelCard({
  caller,
  channel,
  kind,
  names,
  onChanged,
}: {
  caller: Caller
  channel: Channel
  kind: ChannelKindDescription | undefined
  names: ReadonlyMap<string, string>
  onChanged: () => void
}) {
  const id = useId()
  const { channel_id, name, pairing_code: code, pairing_expires_at: expires } = channel
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const [renaming, setRenaming] = useState(false)
  const muted = channel.muted ?? []

  /** Make one change, then read the channels again. Whether the hub took it. */
  const change = async (call: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(undefined)
    try {
      await call()
      onChanged()
      return true
    } catch (error) {
      setFailure(error)
      return false
    } finally {
      setBusy(false)
    }
  }

  return (
    <li aria-label={name}>
      <Card>
        <CardHeader>
          <CardTitle>
            <span className="flex items-center gap-2">
              {name}
              {!channel.enabled && <Badge variant="secondary">Disabled</Badge>}
              {channel.paired && <Badge variant="outline">Paired</Badge>}
            </span>
          </CardTitle>
          <CardDescription>{channel.kind}</CardDescription>
          <CardAction>
            <span className="flex items-center gap-2">
              <Field orientation="horizontal" className="w-auto">
                <Switch
                  id={`${id}-enabled`}
                  checked={channel.enabled}
                  disabled={busy}
                  onCheckedChange={(enabled) =>
                    void change(() => caller.call("channel.update", { channel_id, enabled }))
                  }
                />
                <FieldLabel htmlFor={`${id}-enabled`}>Enabled</FieldLabel>
              </Field>
              <Button
                variant="outline"
                size="sm"
                aria-expanded={renaming}
                onClick={() => setRenaming(!renaming)}
              >
                Rename
              </Button>
              <AlertDialog>
                <AlertDialogTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Delete ${name}`}
                      disabled={busy}
                    />
                  }
                >
                  <Trash2Icon />
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
                    <AlertDialogDescription>
                      The hub deletes its secrets and the notifications it has still to send. A
                      token stays valid at its service.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      variant="destructive"
                      onClick={() =>
                        void change(() => caller.call("channel.delete", { channel_id }))
                      }
                    >
                      Delete
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </span>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4">
            {renaming && (
              <RenameForm
                name={name}
                busy={busy}
                onCancel={() => setRenaming(false)}
                onRename={async (renamed) => {
                  const took = await change(() =>
                    caller.call("channel.update", { channel_id, name: renamed }),
                  )
                  if (took) setRenaming(false)
                }}
              />
            )}
            {code != null && (
              <Alert>
                <AlertTitle>
                  Send <span className="font-mono">{code}</span> to pair it
                </AlertTitle>
                <AlertDescription>
                  The channel sends nothing until you send this code through it.
                  {expires != null && <span> Valid until {shownAt(expires)}.</span>}
                </AlertDescription>
              </Alert>
            )}
            {channel.kind === "discord" && (
              <Field>
                <FieldLabel htmlFor={`${id}-interactions`}>Interactions endpoint URL</FieldLabel>
                <Input
                  id={`${id}-interactions`}
                  readOnly
                  value={`${window.location.origin}/channels/${encodeURIComponent(channel_id)}/discord`}
                />
                <FieldDescription>
                  Paste it into the Discord developer portal. The bot can message you only once it
                  shares a server with you.
                </FieldDescription>
              </Field>
            )}
            <dl className="flex flex-col gap-1 text-sm">
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-muted-foreground">Last delivery</dt>
                <dd>
                  {channel.last_sent_at != null
                    ? shownAt(channel.last_sent_at)
                    : "Nothing sent yet"}
                </dd>
              </div>
              {channel.last_failure != null && (
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-muted-foreground">Last failure</dt>
                  <dd className="text-destructive">
                    {shownAt(channel.last_failure.at)}: {channel.last_failure.error}
                  </dd>
                </div>
              )}
            </dl>
            <section aria-label="Muted instances" className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Muted instances</h3>
              {muted.length === 0 ? (
                <p className="text-sm text-muted-foreground">Mutes no instance</p>
              ) : (
                <span className="flex flex-wrap gap-1.5">
                  {muted.map((instanceId) => {
                    const instance = names.get(instanceId) ?? instanceId
                    return (
                      <Badge key={instanceId} variant="outline">
                        <BellOffIcon data-icon="inline-start" />
                        {instance}
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`Unmute ${instance}`}
                          disabled={busy}
                          onClick={() =>
                            void change(() =>
                              caller.call("channel.unmute", {
                                channel_id,
                                instance_id: instanceId,
                              }),
                            )
                          }
                        >
                          <XIcon />
                        </Button>
                      </Badge>
                    )
                  })}
                </span>
              )}
            </section>
            {failure !== undefined && (
              <Alert variant="destructive">
                <CircleXIcon />
                <AlertTitle>The hub refused it</AlertTitle>
                <AlertDescription>{reason(failure)}</AlertDescription>
              </Alert>
            )}
          </div>
        </CardContent>
        <CardFooter>
          <Button
            variant="outline"
            // A channel waiting for its code sends nothing, a test included.
            disabled={busy || code != null || kind === undefined}
            onClick={() => void change(() => caller.call("channel.test", { channel_id }))}
          >
            {busy && <Spinner data-icon="inline-start" />}
            Send test
          </Button>
        </CardFooter>
      </Card>
    </li>
  )
}

function RenameForm({
  name,
  busy,
  onCancel,
  onRename,
}: {
  name: string
  busy: boolean
  onCancel: () => void
  onRename: (name: string) => Promise<void>
}) {
  const id = useId()
  const [renamed, setRenamed] = useState(name)
  const focused = useCallback((input: HTMLInputElement | null) => input?.focus(), [])

  const rename = (event: FormEvent) => {
    event.preventDefault()
    void onRename(renamed)
  }

  return (
    <form
      aria-label={`Rename ${name}`}
      className="flex flex-wrap items-start gap-2"
      onSubmit={rename}
    >
      <Field className="max-w-sm">
        <FieldLabel htmlFor={`${id}-name`} className="sr-only">
          New name
        </FieldLabel>
        <Input
          id={`${id}-name`}
          autoComplete="off"
          ref={focused}
          value={renamed}
          readOnly={busy}
          onChange={(event) => setRenamed(event.target.value)}
        />
      </Field>
      <span className="ml-auto flex gap-2">
        <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || renamed.trim() === "" || renamed === name}>
          {busy && <Spinner data-icon="inline-start" />}
          Save
        </Button>
      </span>
    </form>
  )
}
