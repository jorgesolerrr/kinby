import { CallError } from "@kinby/contract"
import type { Channel, ChannelKindDescription, Client, Clock, SetupValue } from "@kinby/contract"
import {
  BellIcon,
  BellOffIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  CircleXIcon,
  CopyIcon,
  MailIcon,
  MessageSquareIcon,
  PlusIcon,
  SendIcon,
  XIcon,
} from "lucide-react"
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
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

/** How often the section reads the channels again while one waits for its pairing code. */
const PAIRING_READ_MS = 5_000

/** What the section has open: the add form, or one channel's details. One at a time. */
type Open = { form: "add" } | { details: string }

/** A form's first field takes the focus when the form opens. */
const focus = (element: HTMLElement | null) => element?.focus()

/**
 * The hub's channels, one row each with its status, its Enabled switch and its details folded
 * under it. Add channel opens a form at the top of the list for one of the kinds the hub found. No
 * secret setting is ever read back.
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
  const [open, setOpen] = useState<Open>()
  const adding = open !== undefined && "form" in open
  const unfolded = open !== undefined && "details" in open ? open.details : undefined
  // The user pairs a channel through its service, and nothing tells the section.
  const pairing = listed?.channels.some((channel) => channel.pairing_code != null) ?? false
  useEffect(
    () => (pairing ? clock.after(PAIRING_READ_MS, readAgain) : undefined),
    [pairing, listed, clock, readAgain],
  )

  return (
    <SectionFrame
      title="Channels"
      lead="Where the hub sends notifications. Every enabled channel gets each one."
      actions={
        <Button
          size="sm"
          disabled={adding || listed === undefined}
          onClick={() => setOpen({ form: "add" })}
        >
          <PlusIcon data-icon="inline-start" />
          Add channel
        </Button>
      }
    >
      {adding && listed !== undefined && (
        <AddChannel
          caller={client}
          kinds={listed.kinds}
          onCancel={() => setOpen(undefined)}
          onAdded={(added) => {
            setOpen({ details: added.channel_id })
            readAgain()
          }}
        />
      )}
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : listed.channels.length === 0 ? (
        !adding && (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <BellIcon />
              </EmptyMedia>
              <EmptyTitle>No channels yet</EmptyTitle>
              <EmptyDescription>
                A channel brings kinby's notifications to you when you're away. Add one with Add
                channel.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )
      ) : (
        <ul aria-label="Channels" className="flex flex-col divide-y">
          {listed.channels.map((channel) => (
            <ChannelRow
              key={channel.channel_id}
              caller={client}
              channel={channel}
              kind={listed.kinds.find((kind) => kind.kind === channel.kind)}
              names={listed.names}
              open={unfolded === channel.channel_id}
              onOpenChange={(opened) =>
                setOpen(opened ? { details: channel.channel_id } : undefined)
              }
              onChanged={readAgain}
            />
          ))}
        </ul>
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

/** A new channel at the top of the list: its kind, its name, and the fields its kind describes. */
function AddChannel({
  caller,
  kinds,
  onCancel,
  onAdded,
}: {
  caller: Caller
  kinds: ChannelKindDescription[]
  onCancel: () => void
  onAdded: (channel: Channel) => void
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
      onAdded(
        await caller.call("channel.create", {
          kind: kind.kind,
          name,
          ...createdSettings(kind, values),
        }),
      )
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
    <form
      aria-label="Add a channel"
      className="flex flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={(event) => void add(event)}
    >
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
        <div className="grid gap-3 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor={`${id}-kind`}>Kind</FieldLabel>
            <Select
              value={kind?.kind ?? null}
              onValueChange={(picked) => typeof picked === "string" && pick(picked)}
            >
              <SelectTrigger id={`${id}-kind`} ref={focus}>
                <SelectValue placeholder="Pick one" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {kinds.map((each) => (
                    <SelectItem key={each.kind} value={each.kind}>
                      <KindIcon kind={each.kind} />
                      {kindLabel(each.kind)}
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
        </div>
        {kind?.settings.map((field) => (
          <SetupInput
            key={field.name}
            field={field}
            value={values[field.name] ?? initialInput(field)}
            error={errors[field.name]}
            onChange={(value) => setValues((typed) => ({ ...typed, [field.name]: value }))}
          />
        ))}
        <FieldDescription>
          The hub keeps its secret settings as hub secrets and never shows them again.
          {kind?.pairs && " Once it is added, send it the code its details show to pair it."}
        </FieldDescription>
      </FieldGroup>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" disabled={adding} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={adding || kind === undefined || name.trim() === ""}>
          {adding && <Spinner data-icon="inline-start" />}
          Add channel
        </Button>
      </div>
    </form>
  )
}

/** The kind's icon: a channel kind is a plugin, so one the app doesn't know gets a bell. */
function KindIcon({ kind }: { kind: string }) {
  const Icon =
    kind === "telegram"
      ? SendIcon
      : kind === "discord"
        ? MessageSquareIcon
        : kind === "email"
          ? MailIcon
          : BellIcon
  return <Icon aria-hidden />
}

const KIND_LABELS: Record<string, string> = {
  telegram: "Telegram",
  discord: "Discord",
  email: "Email",
}

/** A channel kind as people write it. A kind with no label here, such as ntfy, shows as named. */
function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
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
 * A channel's status: the first that holds of disabled, waiting for its code, failed and sent. The
 * hub keeps the last failure after a later delivery, so only a failure since then counts.
 */
function status(channel: Channel): { text: string; failed: boolean } {
  const { enabled, pairing_code: code, last_failure: failure, last_sent_at: sent } = channel
  if (!enabled) return { text: "Disabled", failed: false }
  if (code != null) return { text: `Waiting for ${code}`, failed: false }
  if (failure != null && (sent == null || Date.parse(failure.at) > Date.parse(sent)))
    return { text: `Failed ${shownAt(failure.at)}`, failed: true }
  return { text: sent != null ? `Sent ${shownAt(sent)}` : "Nothing sent yet", failed: false }
}

/**
 * One channel's row: its kind, name and status, the Enabled switch, and a chevron that unfolds its
 * details: its pairing, the interactions URL a Discord channel needs, its deliveries and mutes,
 * then Send test, Rename and Delete.
 */
function ChannelRow({
  caller,
  channel,
  kind,
  names,
  open,
  onOpenChange,
  onChanged,
}: {
  caller: Caller
  channel: Channel
  kind: ChannelKindDescription | undefined
  names: ReadonlyMap<string, string>
  open: boolean
  onOpenChange: (open: boolean) => void
  onChanged: () => void
}) {
  const id = useId()
  const { channel_id, name, pairing_code: code, pairing_expires_at: expires } = channel
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const [renaming, setRenaming] = useState(false)
  const muted = channel.muted ?? []
  const shown = status(channel)
  const line = `${kindLabel(channel.kind)} · ${shown.text}`
  const interactions = `${window.location.origin}/channels/${encodeURIComponent(channel_id)}/discord`

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
    <Collapsible open={open} onOpenChange={onOpenChange} render={<li aria-label={name} />}>
      <Item size="row">
        <ItemMedia variant="tile">
          <KindIcon kind={channel.kind} />
        </ItemMedia>
        <ItemContent className="min-w-0">
          <ItemTitle>{name}</ItemTitle>
          <ItemDescription lines="one">
            {shown.failed ? <span className="text-destructive">{line}</span> : line}
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <Switch
            aria-label="Enabled"
            checked={channel.enabled}
            disabled={busy}
            onCheckedChange={(enabled) =>
              void change(() => caller.call("channel.update", { channel_id, enabled }))
            }
          />
          <CollapsibleTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label="Details" />}
          >
            {open ? <ChevronUpIcon /> : <ChevronDownIcon />}
          </CollapsibleTrigger>
        </ItemActions>
        {failure !== undefined && (
          <ItemFooter>
            <Alert variant="destructive">
              <CircleXIcon />
              <AlertTitle>The hub refused it</AlertTitle>
              <AlertDescription>{reason(failure)}</AlertDescription>
            </Alert>
          </ItemFooter>
        )}
      </Item>
      <CollapsibleContent>
        <div className="flex flex-col gap-4 pb-4 pl-11 text-sm">
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
              <div className="flex gap-2">
                <Input id={`${id}-interactions`} variant="mono" readOnly value={interactions} />
                <CopyButton text={interactions} />
              </div>
              <FieldDescription>
                Paste it into the Discord developer portal. The bot can message you only once it
                shares a server with you.
              </FieldDescription>
            </Field>
          )}
          <dl className="flex flex-col gap-1">
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-muted-foreground">Last delivery</dt>
              <dd>
                {channel.last_sent_at != null ? shownAt(channel.last_sent_at) : "Nothing sent yet"}
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
            <h3 className="font-medium">Muted instances</h3>
            {muted.length === 0 ? (
              <p className="text-muted-foreground">Mutes no instance</p>
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
                            caller.call("channel.unmute", { channel_id, instance_id: instanceId }),
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
          {renaming ? (
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
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                // A channel waiting for its code sends nothing, a test included.
                disabled={busy || code != null || !channel.enabled || kind === undefined}
                onClick={() => void change(() => caller.call("channel.test", { channel_id }))}
              >
                {busy && <Spinner data-icon="inline-start" />}
                Send test
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setRenaming(true)}>
                Rename
              </Button>
              <AlertDialog>
                <AlertDialogTrigger
                  render={<Button variant="destructive" size="sm" disabled={busy} />}
                >
                  Delete
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
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2_000)
  }
  return (
    <Button variant="outline" size="icon" aria-label="Copy" onClick={() => void copy()}>
      {copied ? <CheckIcon /> : <CopyIcon />}
    </Button>
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
          ref={focus}
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
