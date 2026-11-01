// PROTOTYPE · Variant A, "Rows". Claude desktop's settings: a plain left list of sections, and the
// open section as one flat list of rows under a header that stays put while the body scrolls. Each
// edit opens in place: Add opens a row at the top of the list, Replace and a channel's details
// unfold under their row. Only a permanent deletion leaves for a confirmation dialog. On a phone
// the dialog fills the screen, opens on the list of sections, and drills into one.
import {
  ArchiveRestoreIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleXIcon,
  CopyIcon,
  KeyRoundIcon,
  MoreHorizontalIcon,
  PlusIcon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react"
import { type ReactNode, useId, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { useIsMobile } from "@/hooks/use-mobile"
import { cn } from "cn"

import {
  CHANNELS,
  CHANNEL_KINDS,
  type Channel,
  NAMES,
  REMOVED,
  RUNNING,
  type Removed,
  SECRETS,
  SECTIONS,
  type Secret,
  type Section,
} from "./mock"
import {
  DeletionPreview,
  fromBar,
  KindIcon,
  type SettingsProps,
  PlanMeters,
  RangeToggle,
  ThemeControl,
  Totals,
  TurnsChart,
  UsageTable,
  kindLabel,
  useBusy,
} from "./shared"

export function SettingsA({ section, onSection, onClose, empty }: SettingsProps) {
  const isMobile = useIsMobile()
  // On a phone the dialog opens on the list, and a section is a screen of its own.
  const [atList, setAtList] = useState(true)
  const shown = section ?? "general"
  const listing = isMobile && atList
  const [adding, setAdding] = useState(false)

  const pick = (picked: Section) => {
    setAdding(false)
    setAtList(false)
    onSection(picked)
  }

  return (
    <Dialog
      open={section !== undefined}
      onOpenChange={(open, details) => {
        if (!open && fromBar(details.event)) return
        if (!open) {
          setAtList(true)
          onClose()
        }
      }}
    >
      <DialogContent
        size="lg"
        className="max-md:max-w-full max-md:rounded-none max-md:data-[size=lg]:h-svh max-md:data-[size=lg]:max-h-svh md:data-[size=lg]:flex-row"
      >
        <DialogDescription className="sr-only">The hub's settings</DialogDescription>
        {(!isMobile || listing) && (
          <nav
            aria-label="Settings sections"
            className="flex shrink-0 flex-col gap-1 p-3 max-md:flex-1 max-md:pt-4 md:w-52 md:border-r md:bg-muted/30"
          >
            <DialogTitle className="px-2 pt-1 pb-3 font-heading text-lg font-semibold">
              Settings
            </DialogTitle>
            {SECTIONS.map((s) => (
              <Button
                key={s.id}
                variant="ghost"
                aria-current={!isMobile && s.id === shown ? "page" : undefined}
                className={cn(
                  "justify-start font-normal max-md:h-12 max-md:text-base",
                  !isMobile && s.id === shown && "bg-muted font-medium",
                )}
                onClick={() => pick(s.id)}
              >
                {s.label}
                {isMobile && <ChevronRightIcon className="ml-auto text-muted-foreground" />}
              </Button>
            ))}
          </nav>
        )}
        {!listing && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <SectionHeader
              section={shown}
              onBack={isMobile ? () => setAtList(true) : undefined}
              onAdd={() => setAdding(true)}
              addable={!adding}
            />
            <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-2 pb-20">
              <Body
                section={shown}
                empty={empty}
                adding={adding}
                onAdded={() => setAdding(false)}
              />
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

const ADDS: Partial<Record<Section, string>> = { secrets: "Add secret", channels: "Add channel" }
const LEADS: Record<Section, string> = {
  general: "How kinby looks on this device.",
  usage: "What your instances used, counted by the hub.",
  secrets: "Values the hub keeps so you set them once. It never shows one back.",
  channels: "Where the hub sends notifications. Every enabled channel gets each one.",
  instances: "Removed instances wait here until you restore them or delete them for good.",
}

/** The section's title and its one action, which stay while the body scrolls. */
function SectionHeader({
  section,
  onBack,
  onAdd,
  addable,
}: {
  section: Section
  onBack?: () => void
  onAdd: () => void
  addable: boolean
}) {
  const label = SECTIONS.find((s) => s.id === section)?.label
  const add = ADDS[section]
  return (
    <header className="flex shrink-0 flex-col gap-1 px-6 pt-5 pr-16 pb-3 max-md:px-4 max-md:pr-14">
      <div className="flex min-h-8 items-center gap-2">
        {onBack !== undefined && (
          <Button variant="ghost" size="icon-sm" aria-label="All settings" onClick={onBack}>
            <ChevronLeftIcon />
          </Button>
        )}
        <h2 className="font-heading text-lg font-semibold">{label}</h2>
        <span className="ml-auto flex items-center gap-2">
          {section === "usage" && (
            <>
              <span className="max-sm:hidden">
                <RangeToggle />
              </span>
              <Button variant="ghost" size="icon-sm" aria-label="Refresh">
                <RefreshCwIcon />
              </Button>
            </>
          )}
          {add !== undefined && (
            <Button size="sm" disabled={!addable} onClick={onAdd}>
              <PlusIcon data-icon="inline-start" />
              {add}
            </Button>
          )}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">{LEADS[section]}</p>
    </header>
  )
}

function Body({
  section,
  empty,
  adding,
  onAdded,
}: {
  section: Section
  empty: boolean
  adding: boolean
  onAdded: () => void
}) {
  switch (section) {
    case "general":
      return <General />
    case "usage":
      return <Usage empty={empty} />
    case "secrets":
      return <Secrets empty={empty} adding={adding} onAdded={onAdded} />
    case "channels":
      return <Channels empty={empty} adding={adding} onAdded={onAdded} />
    case "instances":
      return <Instances empty={empty} />
  }
}

/** A setting's row: its label and description on the left, its control on the right. */
function Row({
  label,
  description,
  children,
}: {
  label: ReactNode
  description?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="font-medium">{label}</span>
        {description !== undefined && (
          <span className="text-sm text-muted-foreground">{description}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function General() {
  const id = useId()
  return (
    <div className="divide-y">
      <Row label={<span id={id}>Theme</span>} description="System follows your device's setting.">
        <ThemeControl labelledBy={id} />
      </Row>
    </div>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 py-4">
      <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

function Usage({ empty }: { empty: boolean }) {
  if (empty)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>Nothing used yet</EmptyTitle>
          <EmptyDescription>No instance ran a turn in the last 7 days.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <div className="divide-y">
      <Group title="Plan limits">
        <PlanMeters />
      </Group>
      <Group title="Last 7 days">
        <Totals />
        <TurnsChart />
      </Group>
      <Group title="By instance">
        <UsageTable />
      </Group>
    </div>
  )
}

// ── Hub secrets ─────────────────────────────────────────────────────────────

function Secrets({
  empty,
  adding,
  onAdded,
}: {
  empty: boolean
  adding: boolean
  onAdded: () => void
}) {
  const [secrets, setSecrets] = useState(empty ? [] : SECRETS)
  const [open, setOpen] = useState<string>()
  return (
    <div className="flex flex-col">
      {adding && (
        <AddSecret
          onCancel={onAdded}
          onAdd={(name) => {
            setSecrets((all) => [{ name, links: [] }, ...all])
            onAdded()
          }}
        />
      )}
      {secrets.length === 0 && !adding ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <KeyRoundIcon />
            </EmptyMedia>
            <EmptyTitle>No hub secrets yet</EmptyTitle>
            <EmptyDescription>
              Add an API key or token once, then link it to any instance that needs it.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="divide-y">
          {secrets.map((secret) => (
            <SecretRow
              key={secret.name}
              secret={secret}
              replacing={open === secret.name}
              onReplace={(on) => setOpen(on ? secret.name : undefined)}
              onDelete={() => setSecrets((all) => all.filter((s) => s.name !== secret.name))}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function AddSecret({ onCancel, onAdd }: { onCancel: () => void; onAdd: (name: string) => void }) {
  const id = useId()
  const [name, setName] = useState("")
  const [value, setValue] = useState("")
  const { busy, run } = useBusy()
  return (
    <form
      aria-label="Add a secret"
      className="mb-2 flex flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        run("add", () => onAdd(name.trim()))
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
          <Input
            id={`${id}-name`}
            autoFocus
            className="font-mono"
            placeholder="NEW_SECRET_NAME"
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-value`}>Value</FieldLabel>
          <Input
            id={`${id}-value`}
            type="password"
            autoComplete="new-password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy !== undefined}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy !== undefined || name === "" || value === ""}>
          {busy !== undefined && <Spinner data-icon="inline-start" />}
          Add secret
        </Button>
      </div>
    </form>
  )
}

function linked(secret: Secret) {
  if (secret.links.length === 0) return "Not linked"
  return secret.links.map((l) => `${NAMES[l.instance] ?? l.instance} · ${l.field}`).join(", ")
}

function SecretRow({
  secret,
  replacing,
  onReplace,
  onDelete,
}: {
  secret: Secret
  replacing: boolean
  onReplace: (on: boolean) => void
  onDelete: () => void
}) {
  const [asking, setAsking] = useState(false)
  return (
    <li className="flex flex-col">
      <div className="flex items-center gap-3 py-3">
        <KeyRoundIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-mono text-sm font-medium">{secret.name}</span>
          <span
            className={cn(
              "truncate text-xs",
              secret.links.length === 0 ? "text-muted-foreground/70" : "text-muted-foreground",
            )}
            title={linked(secret)}
          >
            {linked(secret)}
          </span>
        </div>
        <Button
          variant="outline"
          size="sm"
          aria-expanded={replacing}
          onClick={() => onReplace(!replacing)}
        >
          Replace
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label={`More for ${secret.name}`} />
            }
          >
            <MoreHorizontalIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem variant="destructive" onClick={() => setAsking(true)}>
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {replacing && <ReplaceForm secret={secret} onDone={() => onReplace(false)} />}
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {secret.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The hub deletes its copy. A token stays valid at its provider.
              {secret.links.length > 0 &&
                ` It unlinks ${linked(secret)}, which keep the value they hold.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button variant="destructive" onClick={onDelete}>
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}

function ReplaceForm({ secret, onDone }: { secret: Secret; onDone: () => void }) {
  const id = useId()
  const [value, setValue] = useState("")
  const [recreate, setRecreate] = useState(true)
  const { busy, run } = useBusy()
  const running = [...new Set(secret.links.map((l) => l.instance))].filter((i) => RUNNING.has(i))
  return (
    <form
      aria-label={`Replace ${secret.name}`}
      className="mb-3 ml-7 flex flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        run("replace", onDone)
      }}
    >
      <Field>
        <FieldLabel htmlFor={`${id}-value`}>New value</FieldLabel>
        <Input
          id={`${id}-value`}
          type="password"
          autoFocus
          autoComplete="new-password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </Field>
      {running.length > 0 && (
        <Field orientation="horizontal">
          <Checkbox id={`${id}-recreate`} checked={recreate} onCheckedChange={setRecreate} />
          <FieldLabel htmlFor={`${id}-recreate`} className="font-normal">
            Recreate {running.map((i) => NAMES[i] ?? i).join(", ")} now, so they pick it up
          </FieldLabel>
        </Field>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone} disabled={busy !== undefined}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy !== undefined || value === ""}>
          {busy !== undefined && <Spinner data-icon="inline-start" />}
          Save
        </Button>
      </div>
    </form>
  )
}

// ── Channels ────────────────────────────────────────────────────────────────

function Channels({
  empty,
  adding,
  onAdded,
}: {
  empty: boolean
  adding: boolean
  onAdded: () => void
}) {
  const [channels, setChannels] = useState(empty ? [] : CHANNELS)
  const [open, setOpen] = useState<string>()
  const update = (id: string, change: Partial<Channel>) =>
    setChannels((all) => all.map((c) => (c.id === id ? { ...c, ...change } : c)))
  return (
    <div className="flex flex-col">
      {adding && (
        <AddChannel
          onCancel={onAdded}
          onAdd={(channel) => {
            setChannels((all) => [channel, ...all])
            setOpen(channel.id)
            onAdded()
          }}
        />
      )}
      {channels.length === 0 && !adding ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No channels yet</EmptyTitle>
            <EmptyDescription>
              Add Telegram, Discord or ntfy to hear from kinby when you're away.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="divide-y">
          {channels.map((channel) => (
            <ChannelRow
              key={channel.id}
              channel={channel}
              open={open === channel.id}
              onOpen={(on) => setOpen(on ? channel.id : undefined)}
              onChange={(change) => update(channel.id, change)}
              onDelete={() => setChannels((all) => all.filter((c) => c.id !== channel.id))}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function channelStatus(channel: Channel): { text: string; tone: "muted" | "warn" | "bad" } {
  if (!channel.enabled) return { text: "Disabled", tone: "muted" }
  if (channel.pairingCode !== undefined)
    return { text: `Waiting for ${channel.pairingCode}`, tone: "warn" }
  if (channel.lastFailure !== undefined)
    return { text: `Failed ${channel.lastFailure.at}`, tone: "bad" }
  return {
    text: channel.lastSent !== undefined ? `Sent ${channel.lastSent}` : "Nothing sent yet",
    tone: "muted",
  }
}

function ChannelRow({
  channel,
  open,
  onOpen,
  onChange,
  onDelete,
}: {
  channel: Channel
  open: boolean
  onOpen: (on: boolean) => void
  onChange: (change: Partial<Channel>) => void
  onDelete: () => void
}) {
  const status = channelStatus(channel)
  return (
    <Collapsible render={<li />} open={open} onOpenChange={onOpen}>
      <div className="flex items-center gap-3 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
          <KindIcon kind={channel.kind} />
        </span>
        <button
          type="button"
          className="flex min-w-0 flex-1 flex-col text-left"
          aria-expanded={open}
          onClick={() => onOpen(!open)}
        >
          <span className="truncate font-medium">{channel.name}</span>
          <span
            className={cn(
              "truncate text-xs",
              status.tone === "muted" && "text-muted-foreground",
              status.tone === "warn" && "text-amber-600 dark:text-amber-400",
              status.tone === "bad" && "text-destructive",
            )}
          >
            {kindLabel(channel.kind)} · {status.text}
          </span>
        </button>
        <Switch
          aria-label={`${channel.name} enabled`}
          checked={channel.enabled}
          onCheckedChange={(enabled) => onChange({ enabled })}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={open ? "Hide details" : "Show details"}
          onClick={() => onOpen(!open)}
        >
          <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
        </Button>
      </div>
      <CollapsibleContent>
        <ChannelDetails channel={channel} onChange={onChange} onDelete={onDelete} />
      </CollapsibleContent>
    </Collapsible>
  )
}

function ChannelDetails({
  channel,
  onChange,
  onDelete,
}: {
  channel: Channel
  onChange: (change: Partial<Channel>) => void
  onDelete: () => void
}) {
  const id = useId()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(channel.name)
  const [asking, setAsking] = useState(false)
  const { busy, run } = useBusy()
  const [sent, setSent] = useState(false)
  return (
    <div className="mb-4 ml-11 flex flex-col gap-4 text-sm">
      {channel.pairingCode !== undefined && (
        <Alert>
          <AlertTitle>
            Send <span className="font-mono">{channel.pairingCode}</span> to pair it
          </AlertTitle>
          <AlertDescription>
            The channel sends nothing until you send this code through it. Valid until{" "}
            {channel.pairingUntil}.
          </AlertDescription>
        </Alert>
      )}
      {channel.kind === "discord" && (
        <Field>
          <FieldLabel htmlFor={`${id}-url`}>Interactions endpoint URL</FieldLabel>
          <div className="flex gap-2">
            <Input
              id={`${id}-url`}
              readOnly
              className="font-mono text-xs"
              value={`https://kinby.jorgesolerrr.dev/channels/${channel.id}/discord`}
            />
            <Button variant="outline" size="icon" aria-label="Copy">
              <CopyIcon />
            </Button>
          </div>
          <FieldDescription>Paste it into the Discord developer portal.</FieldDescription>
        </Field>
      )}
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">Last delivery</dt>
        <dd>{channel.lastSent ?? "Nothing sent yet"}</dd>
        {channel.lastFailure !== undefined && (
          <>
            <dt className="text-muted-foreground">Last failure</dt>
            <dd className="text-destructive">
              {channel.lastFailure.at}: {channel.lastFailure.error}
            </dd>
          </>
        )}
        <dt className="text-muted-foreground">Muted</dt>
        <dd className="flex flex-wrap gap-1.5">
          {channel.muted.length === 0
            ? "No instance"
            : channel.muted.map((i) => (
                <Badge key={i} variant="outline">
                  {NAMES[i] ?? i}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Unmute ${NAMES[i] ?? i}`}
                    onClick={() => onChange({ muted: channel.muted.filter((m) => m !== i) })}
                  >
                    <XIcon />
                  </Button>
                </Badge>
              ))}
        </dd>
      </dl>
      {renaming ? (
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            onChange({ name })
            setRenaming(false)
          }}
        >
          <Input
            aria-label="New name"
            autoFocus
            className="max-w-xs"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="button" variant="ghost" onClick={() => setRenaming(false)}>
            Cancel
          </Button>
          <Button type="submit" disabled={name.trim() === "" || name === channel.name}>
            Save
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy !== undefined || channel.pairingCode !== undefined || !channel.enabled}
            onClick={() => run("test", () => setSent(true))}
          >
            {busy !== undefined && <Spinner data-icon="inline-start" />}
            Send test
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setRenaming(true)}>
            Rename
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setAsking(true)}
          >
            Delete
          </Button>
          {sent && <span className="text-muted-foreground">Test sent.</span>}
        </div>
      )}
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {channel.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The hub deletes its secrets and the notifications it has still to send. A token stays
              valid at its service.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button variant="destructive" onClick={onDelete}>
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function AddChannel({
  onCancel,
  onAdd,
}: {
  onCancel: () => void
  onAdd: (channel: Channel) => void
}) {
  const id = useId()
  const [kind, setKind] = useState<string | null>(null)
  const [name, setName] = useState("")
  const { busy, run } = useBusy()
  const described = CHANNEL_KINDS.find((k) => k.kind === kind)
  return (
    <form
      aria-label="Add a channel"
      className="mb-2 flex flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (described === undefined) return
        run("add", () =>
          onAdd({
            id: `new-${Date.now()}`,
            name,
            kind: described.kind,
            enabled: true,
            paired: false,
            pairingCode: described.pairs ? "KIN-7730" : undefined,
            pairingUntil: described.pairs ? "Today, 19:05" : undefined,
            muted: [],
          }),
        )
      }}
    >
      <FieldGroup className="gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor={`${id}-kind`}>Kind</FieldLabel>
            <Select value={kind} onValueChange={(v) => setKind(v as string | null)}>
              <SelectTrigger id={`${id}-kind`} className="w-full">
                <SelectValue placeholder="Pick one">
                  {(v: string | null) => (v === null ? "Pick one" : kindLabel(v))}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {CHANNEL_KINDS.map((k) => (
                  <SelectItem key={k.kind} value={k.kind}>
                    <KindIcon kind={k.kind} />
                    {kindLabel(k.kind)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
            <Input
              id={`${id}-name`}
              autoComplete="off"
              placeholder="My phone"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
        </div>
        {described?.fields.map((f) => (
          <Field key={f.name}>
            <FieldLabel htmlFor={`${id}-${f.name}`}>{f.label}</FieldLabel>
            <Input
              id={`${id}-${f.name}`}
              type={f.secret ? "password" : "text"}
              placeholder={f.placeholder}
              autoComplete={f.secret ? "new-password" : "off"}
            />
            {f.secret && <FieldDescription>Kept as a hub secret.</FieldDescription>}
          </Field>
        ))}
        {described?.pairs && (
          <FieldDescription>
            Once it is added, send it the code it shows to pair it with you.
          </FieldDescription>
        )}
      </FieldGroup>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy !== undefined}>
          Cancel
        </Button>
        <Button
          type="submit"
          disabled={busy !== undefined || described === undefined || name.trim() === ""}
        >
          {busy !== undefined && <Spinner data-icon="inline-start" />}
          Add channel
        </Button>
      </div>
    </form>
  )
}

// ── Instances ───────────────────────────────────────────────────────────────

function Instances({ empty }: { empty: boolean }) {
  const [removed, setRemoved] = useState(empty ? [] : REMOVED)
  const [restored, setRestored] = useState<string[]>([])
  if (removed.length === 0 && restored.length === 0)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ArchiveRestoreIcon />
          </EmptyMedia>
          <EmptyTitle>No removed instances</EmptyTitle>
          <EmptyDescription>
            An instance you remove shows here, and you can restore it.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <ul className="divide-y">
      {restored.map((name) => (
        <li key={name} className="flex items-center gap-3 py-3 text-sm">
          <ArchiveRestoreIcon className="size-4 text-muted-foreground" />
          <span className="flex-1">
            <span className="font-medium">{name}</span> is back, stopped.
          </span>
          <Button variant="outline" size="sm">
            Open
          </Button>
        </li>
      ))}
      {removed.map((instance) => (
        <RemovedRow
          key={instance.id}
          instance={instance}
          onRestored={() => {
            setRemoved((all) => all.filter((r) => r.id !== instance.id))
            setRestored((all) => [...all, instance.name])
          }}
          onDeleted={() => setRemoved((all) => all.filter((r) => r.id !== instance.id))}
        />
      ))}
    </ul>
  )
}

function RemovedRow({
  instance,
  onRestored,
  onDeleted,
}: {
  instance: Removed
  onRestored: () => void
  onDeleted: () => void
}) {
  const { busy, run } = useBusy(1400)
  const [asking, setAsking] = useState(false)
  const [typed, setTyped] = useState("")
  const [failed, setFailed] = useState(false)
  return (
    <li className="flex flex-col">
      <div className="flex items-center gap-3 py-3">
        <InstanceAvatar avatar={instance.avatar} name={instance.name} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-medium">{instance.name}</span>
          <span className="text-xs text-muted-foreground">Removed {instance.removed}</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={busy !== undefined}
          onClick={() =>
            run("restore", () => {
              // The second one fails, to show where a failure lands.
              if (instance.id === "old-scout" && !failed) setFailed(true)
              else onRestored()
            })
          }
        >
          {busy !== undefined && <Spinner data-icon="inline-start" />}
          Restore
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label={`More for ${instance.name}`} />
            }
          >
            <MoreHorizontalIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem variant="destructive" onClick={() => setAsking(true)}>
              Delete permanently…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {failed && (
        <Alert variant="destructive" className="mb-3">
          <CircleXIcon />
          <AlertTitle>The restoration failed</AlertTitle>
          <AlertDescription>
            The volume kinby-old-scout-memory is in use by another container. Restore again to
            retry.
          </AlertDescription>
        </Alert>
      )}
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent className="sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {instance.name} permanently</AlertDialogTitle>
            <AlertDialogDescription>
              The hub deletes these directories and volumes, and the deletion can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <DeletionPreview instance={instance} typed={typed} onTyped={setTyped} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button variant="destructive" disabled={typed !== instance.name} onClick={onDeleted}>
              Delete permanently
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}
