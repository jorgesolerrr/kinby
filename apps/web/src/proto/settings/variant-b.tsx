// PROTOTYPE · Variant B, "Drill-in". macOS System Settings and ChatGPT's settings: a left list of
// sections with icons, and the open section as grouped cards of rows. A list stays read-only: a
// secret, a channel, Add and Delete permanently each open a page of their own inside the pane,
// with a breadcrumb back and a footer that holds Save while the page scrolls. On a phone the
// dialog fills the screen and the sections are a picker in its header.
import {
  ArchiveRestoreIcon,
  BellIcon,
  BoxesIcon,
  ChartColumnIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleXIcon,
  CopyIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  Settings2Icon,
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
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
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
  USAGE_ROWS,
} from "./mock"
import {
  DeletionPreview,
  fromBar,
  KindIcon,
  PlanMeters,
  RangeToggle,
  type SettingsProps,
  ThemeControl,
  Totals,
  TurnsChart,
  kindLabel,
  useBusy,
} from "./shared"

const ICONS: Record<Section, typeof Settings2Icon> = {
  general: Settings2Icon,
  usage: ChartColumnIcon,
  secrets: KeyRoundIcon,
  channels: BellIcon,
  instances: BoxesIcon,
}

/** A page pushed over a section's list. */
type Page =
  | { kind: "secret"; name: string }
  | { kind: "add-secret" }
  | { kind: "channel"; id: string }
  | { kind: "add-channel" }
  | { kind: "delete-instance"; id: string }

/** The section's data, held here so a sub-page's change shows on the list it returns to. */
function useHub(empty: boolean) {
  const [secrets, setSecrets] = useState(empty ? [] : SECRETS)
  const [channels, setChannels] = useState(empty ? [] : CHANNELS)
  const [removed, setRemoved] = useState(empty ? [] : REMOVED)
  return { secrets, setSecrets, channels, setChannels, removed, setRemoved }
}
type Hub = ReturnType<typeof useHub>

export function SettingsB({ section, onSection, onClose, empty }: SettingsProps) {
  const isMobile = useIsMobile()
  const shown = section ?? "general"
  const [page, setPage] = useState<Page>()
  const hub = useHub(empty)
  const [hubKey, setHubKey] = useState(empty)
  // The Full/Empty toggle starts the data again.
  if (hubKey !== empty) {
    setHubKey(empty)
    hub.setSecrets(empty ? [] : SECRETS)
    hub.setChannels(empty ? [] : CHANNELS)
    hub.setRemoved(empty ? [] : REMOVED)
  }

  const pick = (picked: Section) => {
    setPage(undefined)
    onSection(picked)
  }

  return (
    <Dialog
      open={section !== undefined}
      onOpenChange={(open, details) => {
        if (!open && fromBar(details.event)) return
        if (!open) {
          setPage(undefined)
          onClose()
        }
      }}
    >
      <DialogContent
        size="lg"
        className="max-md:max-w-full max-md:rounded-none max-md:data-[size=lg]:h-svh max-md:data-[size=lg]:max-h-svh md:data-[size=lg]:flex-row"
      >
        <DialogDescription className="sr-only">The hub's settings</DialogDescription>
        {isMobile ? (
          <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3 pr-14">
            <DialogTitle className="sr-only">Settings</DialogTitle>
            <Select value={shown} onValueChange={(v) => pick(v as Section)}>
              <SelectTrigger
                aria-label="Section"
                className="w-auto font-heading text-base font-semibold"
              >
                <SelectValue>{(v: Section) => SECTIONS.find((s) => s.id === v)?.label}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {SECTIONS.map((s) => {
                  const Icon = ICONS[s.id]
                  return (
                    <SelectItem key={s.id} value={s.id}>
                      <Icon />
                      {s.label}
                    </SelectItem>
                  )
                })}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <nav
            aria-label="Settings sections"
            className="flex w-56 shrink-0 flex-col gap-0.5 border-r p-3"
          >
            <DialogTitle className="px-2 pt-1 pb-3 font-heading text-lg font-semibold">
              Settings
            </DialogTitle>
            {SECTIONS.map((s) => {
              const Icon = ICONS[s.id]
              const active = s.id === shown
              return (
                <Button
                  key={s.id}
                  variant="ghost"
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "h-10 justify-start gap-2.5 font-normal",
                    active && "bg-muted font-medium",
                  )}
                  onClick={() => pick(s.id)}
                >
                  <span
                    className={cn(
                      "flex size-6 items-center justify-center rounded-md border bg-background",
                      active && "border-primary/40 text-primary",
                    )}
                  >
                    <Icon className="size-3.5" />
                  </span>
                  {s.label}
                </Button>
              )
            })}
          </nav>
        )}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {page === undefined ? (
            <SectionPage
              section={shown}
              hub={hub}
              onPage={setPage}
              isMobile={isMobile}
              empty={empty}
            />
          ) : (
            <SubPage
              section={shown}
              page={page}
              hub={hub}
              onBack={() => setPage(undefined)}
              onPage={setPage}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ── Page frame ──────────────────────────────────────────────────────────────

/** A page in the pane: a header that stays, a body that scrolls, and a footer that stays. */
function Frame({
  title,
  crumb,
  onBack,
  actions,
  footer,
  children,
}: {
  title: ReactNode
  crumb?: string
  onBack?: () => void
  actions?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <header className="flex min-h-16 shrink-0 items-center gap-2 border-b px-6 py-3 pr-16 max-md:px-4 max-md:pr-4">
        {onBack !== undefined && (
          <Button variant="ghost" size="icon-sm" aria-label={`Back to ${crumb}`} onClick={onBack}>
            <ChevronLeftIcon />
          </Button>
        )}
        <div className="flex min-w-0 flex-col">
          {crumb !== undefined && <span className="text-xs text-muted-foreground">{crumb}</span>}
          <h2 className="truncate font-heading text-lg leading-tight font-semibold">{title}</h2>
        </div>
        {actions !== undefined && (
          <span className="ml-auto flex items-center gap-2">{actions}</span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/20 px-6 py-5 max-md:px-4">
        <div className="mx-auto flex max-w-2xl flex-col gap-6 pb-16">{children}</div>
      </div>
      {footer !== undefined && (
        <footer className="flex shrink-0 justify-end gap-2 border-t bg-popover px-6 py-3 max-md:px-4">
          {footer}
        </footer>
      )}
    </>
  )
}

/** A labelled card of rows. */
function Group({ label, note, children }: { label?: string; note?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      {label !== undefined && (
        <h3 className="px-1 text-sm font-medium text-muted-foreground">{label}</h3>
      )}
      <div className="divide-y rounded-xl border bg-card">{children}</div>
      {note !== undefined && <p className="px-1 text-xs text-muted-foreground">{note}</p>}
    </section>
  )
}

function Row({
  children,
  onClick,
  className,
}: {
  children: ReactNode
  onClick?: () => void
  className?: string
}) {
  const body = (
    <div className={cn("flex min-h-14 items-center gap-3 px-4 py-3", className)}>{children}</div>
  )
  if (onClick === undefined) return body
  return (
    <button
      type="button"
      className="block w-full text-left first:rounded-t-xl last:rounded-b-xl hover:bg-muted/50"
      onClick={onClick}
    >
      {body}
    </button>
  )
}

function RowText({
  title,
  description,
  mono,
}: {
  title: ReactNode
  description?: ReactNode
  mono?: boolean
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className={cn("truncate font-medium", mono && "font-mono text-sm")}>{title}</span>
      {description !== undefined && (
        <span className="truncate text-xs text-muted-foreground">{description}</span>
      )}
    </div>
  )
}

const Chevron = () => (
  <ChevronRightIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
)

// ── Sections ────────────────────────────────────────────────────────────────

function SectionPage({
  section,
  hub,
  onPage,
  isMobile,
  empty,
}: {
  section: Section
  hub: Hub
  onPage: (page: Page) => void
  isMobile: boolean
  empty: boolean
}) {
  const label = SECTIONS.find((s) => s.id === section)?.label
  const title = isMobile ? undefined : label
  switch (section) {
    case "general":
      return <GeneralB title={title} />
    case "usage":
      return (
        <Frame
          title={title ?? "Last 7 days"}
          actions={
            <>
              <span className="max-sm:hidden">
                <RangeToggle />
              </span>
              <Button variant="ghost" size="icon-sm" aria-label="Refresh">
                <RefreshCwIcon />
              </Button>
            </>
          }
        >
          <UsageB empty={empty} />
        </Frame>
      )
    case "secrets":
      return (
        <Frame
          title={title ?? "Hub secrets"}
          actions={
            <Button size="sm" onClick={() => onPage({ kind: "add-secret" })}>
              <PlusIcon data-icon="inline-start" />
              Add secret
            </Button>
          }
        >
          <SecretList secrets={hub.secrets} onPage={onPage} />
        </Frame>
      )
    case "channels":
      return (
        <Frame
          title={title ?? "Channels"}
          actions={
            <Button size="sm" onClick={() => onPage({ kind: "add-channel" })}>
              <PlusIcon data-icon="inline-start" />
              Add channel
            </Button>
          }
        >
          <ChannelList hub={hub} onPage={onPage} />
        </Frame>
      )
    case "instances":
      return (
        <Frame title={title ?? "Removed instances"}>
          <RemovedList hub={hub} onPage={onPage} />
        </Frame>
      )
  }
}

function GeneralB({ title }: { title?: string }) {
  const id = useId()
  return (
    <Frame title={title ?? "General"}>
      <Group label="Appearance">
        <Row className="flex-wrap">
          <RowText title={<span id={id}>Theme</span>} description="System follows your device." />
          <ThemeControl labelledBy={id} />
        </Row>
      </Group>
    </Frame>
  )
}

function UsageB({ empty }: { empty: boolean }) {
  if (empty)
    return (
      <Empty className="rounded-xl border bg-card">
        <EmptyHeader>
          <EmptyTitle>Nothing used yet</EmptyTitle>
          <EmptyDescription>No instance ran a turn in the last 7 days.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <>
      <Totals />
      <Group label="Plan limits">
        <div className="p-4">
          <PlanMeters />
        </div>
      </Group>
      <Group label="Turns per day">
        <div className="p-4">
          <TurnsChart />
        </div>
      </Group>
      <Group label="By instance" note="Open one to see its Stats tab.">
        {USAGE_ROWS.map((row) => (
          <Row key={row.instance} onClick={() => {}}>
            <RowText
              title={row.instance}
              description={
                row.notCounted !== undefined
                  ? `Not counted: ${row.notCounted}`
                  : `${(row.claude ?? 0) + (row.chatgpt ?? 0)} plan runs · ${row.factory} factory runs`
              }
            />
            {row.notCounted === undefined && (
              <span className="text-right text-sm tabular-nums">
                {row.turns} turns
                <span className="block text-xs text-muted-foreground">{row.cost}</span>
              </span>
            )}
            <Chevron />
          </Row>
        ))}
      </Group>
    </>
  )
}

const linkCount = (secret: Secret) =>
  secret.links.length === 0
    ? "Not linked"
    : `${new Set(secret.links.map((l) => l.instance)).size} instance${secret.links.length === 1 ? "" : "s"}`

function SecretList({ secrets, onPage }: { secrets: Secret[]; onPage: (page: Page) => void }) {
  if (secrets.length === 0)
    return (
      <Empty className="rounded-xl border bg-card">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <KeyRoundIcon />
          </EmptyMedia>
          <EmptyTitle>No hub secrets yet</EmptyTitle>
          <EmptyDescription>
            Add an API key or token once, then link it to any instance that needs it.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={() => onPage({ kind: "add-secret" })}>
            <PlusIcon data-icon="inline-start" />
            Add secret
          </Button>
        </EmptyContent>
      </Empty>
    )
  return (
    <Group note="The hub keeps each value and never shows one back.">
      {secrets.map((secret) => (
        <Row key={secret.name} onClick={() => onPage({ kind: "secret", name: secret.name })}>
          <KeyRoundIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          <RowText title={secret.name} mono />
          <Badge variant={secret.links.length === 0 ? "outline" : "secondary"}>
            {linkCount(secret)}
          </Badge>
          <Chevron />
        </Row>
      ))}
    </Group>
  )
}

function channelLine(channel: Channel) {
  if (!channel.enabled) return <span>Disabled</span>
  if (channel.pairingCode !== undefined)
    return (
      <span className="text-amber-600 dark:text-amber-400">Waiting for {channel.pairingCode}</span>
    )
  if (channel.lastFailure !== undefined)
    return <span className="text-destructive">Failed {channel.lastFailure.at}</span>
  return (
    <span>{channel.lastSent !== undefined ? `Sent ${channel.lastSent}` : "Nothing sent yet"}</span>
  )
}

function ChannelList({ hub, onPage }: { hub: Hub; onPage: (page: Page) => void }) {
  if (hub.channels.length === 0)
    return (
      <Empty className="rounded-xl border bg-card">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <BellIcon />
          </EmptyMedia>
          <EmptyTitle>No channels yet</EmptyTitle>
          <EmptyDescription>
            Add Telegram, Discord or ntfy to hear from kinby when you're away.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={() => onPage({ kind: "add-channel" })}>
            <PlusIcon data-icon="inline-start" />
            Add channel
          </Button>
        </EmptyContent>
      </Empty>
    )
  return (
    <Group note="Every enabled channel gets each notification.">
      {hub.channels.map((channel) => (
        <div
          key={channel.id}
          className="flex items-center first:rounded-t-xl last:rounded-b-xl hover:bg-muted/50"
        >
          <button
            type="button"
            className="flex min-h-14 min-w-0 flex-1 items-center gap-3 py-3 pl-4 text-left"
            onClick={() => onPage({ kind: "channel", id: channel.id })}
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
              <KindIcon kind={channel.kind} />
            </span>
            <RowText
              title={channel.name}
              description={
                <>
                  {kindLabel(channel.kind)} · {channelLine(channel)}
                </>
              }
            />
          </button>
          <span className="flex items-center gap-3 pr-4 pl-3">
            <Switch
              aria-label={`${channel.name} enabled`}
              checked={channel.enabled}
              onCheckedChange={(enabled) =>
                hub.setChannels((all) =>
                  all.map((c) => (c.id === channel.id ? { ...c, enabled } : c)),
                )
              }
            />
            <Chevron />
          </span>
        </div>
      ))}
    </Group>
  )
}

function RemovedList({ hub, onPage }: { hub: Hub; onPage: (page: Page) => void }) {
  const { busy, run } = useBusy(1400)
  const [restored, setRestored] = useState<string[]>([])
  const [failed, setFailed] = useState<string>()
  if (hub.removed.length === 0 && restored.length === 0)
    return (
      <Empty className="rounded-xl border bg-card">
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
    <>
      {restored.map((name) => (
        <Alert key={name}>
          <CheckIcon />
          <AlertTitle>{name} is back</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            It comes back stopped.
            <Button variant="outline" size="sm">
              Open {name}
            </Button>
          </AlertDescription>
        </Alert>
      ))}
      {hub.removed.length > 0 && (
        <Group note="Removed instances wait here until you restore them or delete them for good.">
          {hub.removed.map((instance) => (
            <div key={instance.id} className="flex flex-col">
              <Row>
                <InstanceAvatar avatar={instance.avatar} name={instance.name} />
                <RowText title={instance.name} description={`Removed ${instance.removed}`} />
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive max-sm:hidden"
                  onClick={() => onPage({ kind: "delete-instance", id: instance.id })}
                >
                  Delete…
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy === instance.id}
                  onClick={() =>
                    run(instance.id, () => {
                      if (instance.id === "old-scout" && failed === undefined) {
                        setFailed(instance.id)
                        return
                      }
                      hub.setRemoved((all) => all.filter((r) => r.id !== instance.id))
                      setRestored((all) => [...all, instance.name])
                    })
                  }
                >
                  {busy === instance.id && <Spinner data-icon="inline-start" />}
                  Restore
                </Button>
              </Row>
              {failed === instance.id && (
                <div className="px-4 pb-3">
                  <Alert variant="destructive">
                    <CircleXIcon />
                    <AlertTitle>The restoration failed</AlertTitle>
                    <AlertDescription>
                      The volume kinby-old-scout-memory is in use by another container. Restore
                      again to retry.
                    </AlertDescription>
                  </Alert>
                </div>
              )}
            </div>
          ))}
        </Group>
      )}
    </>
  )
}

// ── Sub-pages ───────────────────────────────────────────────────────────────

function SubPage({
  section,
  page,
  hub,
  onBack,
  onPage,
}: {
  section: Section
  page: Page
  hub: Hub
  onBack: () => void
  onPage: (page: Page | undefined) => void
}) {
  const crumb = SECTIONS.find((s) => s.id === section)?.label ?? ""
  switch (page.kind) {
    case "add-secret":
      return <AddSecretPage crumb={crumb} hub={hub} onBack={onBack} />
    case "secret": {
      const secret = hub.secrets.find((s) => s.name === page.name)
      if (secret === undefined) return null
      return <SecretPage crumb={crumb} secret={secret} hub={hub} onBack={onBack} />
    }
    case "add-channel":
      return <AddChannelPage crumb={crumb} hub={hub} onBack={onBack} onPage={onPage} />
    case "channel": {
      const channel = hub.channels.find((c) => c.id === page.id)
      if (channel === undefined) return null
      return <ChannelPage crumb={crumb} channel={channel} hub={hub} onBack={onBack} />
    }
    case "delete-instance": {
      const instance = hub.removed.find((r) => r.id === page.id)
      if (instance === undefined) return null
      return <DeleteInstancePage crumb={crumb} instance={instance} hub={hub} onBack={onBack} />
    }
  }
}

function AddSecretPage({ crumb, hub, onBack }: { crumb: string; hub: Hub; onBack: () => void }) {
  const id = useId()
  const [name, setName] = useState("")
  const [value, setValue] = useState("")
  const { busy, run } = useBusy()
  return (
    <Frame
      title="Add secret"
      crumb={crumb}
      onBack={onBack}
      footer={
        <>
          <Button variant="ghost" onClick={onBack} disabled={busy !== undefined}>
            Cancel
          </Button>
          <Button
            disabled={busy !== undefined || name === "" || value === ""}
            onClick={() =>
              run("add", () => {
                hub.setSecrets((all) => [{ name, links: [] }, ...all])
                onBack()
              })
            }
          >
            {busy !== undefined && <Spinner data-icon="inline-start" />}
            Add secret
          </Button>
        </>
      }
    >
      <Group>
        <div className="flex flex-col gap-5 p-4">
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
            <FieldDescription>
              Capitals, digits and underscores. An instance's config picks it by this name.
            </FieldDescription>
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
            <FieldDescription>The hub keeps it and never shows it back.</FieldDescription>
          </Field>
        </div>
      </Group>
    </Frame>
  )
}

function SecretPage({
  crumb,
  secret,
  hub,
  onBack,
}: {
  crumb: string
  secret: Secret
  hub: Hub
  onBack: () => void
}) {
  const id = useId()
  const [replacing, setReplacing] = useState(false)
  const [value, setValue] = useState("")
  const [recreate, setRecreate] = useState(true)
  const [asking, setAsking] = useState(false)
  const [saved, setSaved] = useState(false)
  const { busy, run } = useBusy()
  const running = [...new Set(secret.links.map((l) => l.instance))].filter((i) => RUNNING.has(i))
  return (
    <Frame
      title={<span className="font-mono text-base">{secret.name}</span>}
      crumb={crumb}
      onBack={onBack}
      footer={
        replacing ? (
          <>
            <Button
              variant="ghost"
              onClick={() => setReplacing(false)}
              disabled={busy !== undefined}
            >
              Cancel
            </Button>
            <Button
              disabled={busy !== undefined || value === ""}
              onClick={() =>
                run("save", () => {
                  setReplacing(false)
                  setValue("")
                  setSaved(true)
                })
              }
            >
              {busy !== undefined && <Spinner data-icon="inline-start" />}
              Save value
            </Button>
          </>
        ) : undefined
      }
    >
      <Group label="Value">
        {replacing ? (
          <div className="flex flex-col gap-4 p-4">
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
          </div>
        ) : (
          <Row>
            <RowText
              title="••••••••••••"
              description={saved ? "Replaced just now" : "Set · the hub never shows it back"}
            />
            <Button variant="outline" size="sm" onClick={() => setReplacing(true)}>
              Replace
            </Button>
          </Row>
        )}
      </Group>
      <Group label="Linked to">
        {secret.links.length === 0 ? (
          <Row>
            <RowText title="Not linked" description="Link it from an instance's Config tab." />
          </Row>
        ) : (
          secret.links.map((link) => (
            <Row key={`${link.instance} ${link.field}`}>
              <RowText
                title={NAMES[link.instance] ?? link.instance}
                description={<span className="font-mono">{link.field}</span>}
              />
              {RUNNING.has(link.instance) && <Badge variant="secondary">Running</Badge>}
            </Row>
          ))
        )}
      </Group>
      <Group>
        <Row>
          <RowText
            title="Delete secret"
            description="Instances linked to it keep the value they hold."
          />
          <Button variant="destructive" size="sm" onClick={() => setAsking(true)}>
            Delete
          </Button>
        </Row>
      </Group>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {secret.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The hub deletes its copy. A token stays valid at its provider.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={() => {
                hub.setSecrets((all) => all.filter((s) => s.name !== secret.name))
                onBack()
              }}
            >
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Frame>
  )
}

function AddChannelPage({
  crumb,
  hub,
  onBack,
  onPage,
}: {
  crumb: string
  hub: Hub
  onBack: () => void
  onPage: (page: Page | undefined) => void
}) {
  const id = useId()
  const [kind, setKind] = useState<string>()
  const [name, setName] = useState("")
  const { busy, run } = useBusy()
  const described = CHANNEL_KINDS.find((k) => k.kind === kind)
  return (
    <Frame
      title="Add channel"
      crumb={crumb}
      onBack={onBack}
      footer={
        <>
          <Button variant="ghost" onClick={onBack} disabled={busy !== undefined}>
            Cancel
          </Button>
          <Button
            disabled={busy !== undefined || described === undefined || name.trim() === ""}
            onClick={() =>
              described !== undefined &&
              run("add", () => {
                const channel: Channel = {
                  id: `new-${Date.now()}`,
                  name,
                  kind: described.kind,
                  enabled: true,
                  paired: false,
                  pairingCode: described.pairs ? "KIN-7730" : undefined,
                  pairingUntil: described.pairs ? "Today, 19:05" : undefined,
                  muted: [],
                }
                hub.setChannels((all) => [channel, ...all])
                // A new channel opens on its page, where its pairing code waits.
                onPage({ kind: "channel", id: channel.id })
              })
            }
          >
            {busy !== undefined && <Spinner data-icon="inline-start" />}
            Add channel
          </Button>
        </>
      }
    >
      <section className="flex flex-col gap-2">
        <h3 className="px-1 text-sm font-medium text-muted-foreground">Kind</h3>
        <div role="radiogroup" aria-label="Kind" className="grid gap-2 sm:grid-cols-3">
          {CHANNEL_KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              role="radio"
              aria-checked={kind === k.kind}
              onClick={() => setKind(k.kind)}
              className={cn(
                "flex flex-col items-start gap-2 rounded-xl border bg-card p-3 text-left hover:bg-muted/50",
                kind === k.kind && "border-primary ring-3 ring-primary/20",
              )}
            >
              <KindIcon kind={k.kind} className="size-5" />
              <span className="font-medium">{kindLabel(k.kind)}</span>
              <span className="text-xs text-muted-foreground">
                {k.pairs ? "Pairs with a code" : "No pairing"}
              </span>
            </button>
          ))}
        </div>
      </section>
      {described !== undefined && (
        <Group label="Details">
          <div className="flex flex-col gap-5 p-4">
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
            {described.fields.map((f) => (
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
          </div>
        </Group>
      )}
    </Frame>
  )
}

function ChannelPage({
  crumb,
  channel,
  hub,
  onBack,
}: {
  crumb: string
  channel: Channel
  hub: Hub
  onBack: () => void
}) {
  const id = useId()
  const [name, setName] = useState(channel.name)
  const [asking, setAsking] = useState(false)
  const [sent, setSent] = useState(false)
  const { busy, run } = useBusy()
  const update = (change: Partial<Channel>) =>
    hub.setChannels((all) => all.map((c) => (c.id === channel.id ? { ...c, ...change } : c)))
  return (
    <Frame
      title={channel.name}
      crumb={crumb}
      onBack={onBack}
      actions={
        <Switch
          aria-label="Enabled"
          checked={channel.enabled}
          onCheckedChange={(enabled) => update({ enabled })}
        />
      }
      footer={
        name !== channel.name ? (
          <>
            <Button variant="ghost" onClick={() => setName(channel.name)}>
              Cancel
            </Button>
            <Button disabled={name.trim() === ""} onClick={() => update({ name })}>
              Save name
            </Button>
          </>
        ) : undefined
      }
    >
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
      <Group label="Channel">
        <div className="flex flex-col gap-5 p-4">
          <Field>
            <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
            <Input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
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
        </div>
      </Group>
      <Group label="Delivery">
        <Row>
          <RowText title="Last delivery" description={channel.lastSent ?? "Nothing sent yet"} />
        </Row>
        {channel.lastFailure !== undefined && (
          <Row>
            <RowText
              title="Last failure"
              description={
                <span className="whitespace-normal text-destructive">
                  {channel.lastFailure.at}: {channel.lastFailure.error}
                </span>
              }
            />
          </Row>
        )}
        <Row>
          <RowText
            title="Send a test"
            description={sent ? "Test sent." : "Check it reaches you."}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={busy !== undefined || channel.pairingCode !== undefined || !channel.enabled}
            onClick={() => run("test", () => setSent(true))}
          >
            {busy !== undefined && <Spinner data-icon="inline-start" />}
            Send test
          </Button>
        </Row>
      </Group>
      <Group label="Muted instances" note="A muted instance's notifications skip this channel.">
        {channel.muted.length === 0 ? (
          <Row>
            <RowText title="No instance is muted" />
          </Row>
        ) : (
          channel.muted.map((i) => (
            <Row key={i}>
              <RowText title={NAMES[i] ?? i} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => update({ muted: channel.muted.filter((m) => m !== i) })}
              >
                Unmute
              </Button>
            </Row>
          ))
        )}
      </Group>
      <Group>
        <Row>
          <RowText
            title="Delete channel"
            description="Its secrets and unsent notifications go with it."
          />
          <Button variant="destructive" size="sm" onClick={() => setAsking(true)}>
            Delete
          </Button>
        </Row>
      </Group>
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
            <Button
              variant="destructive"
              onClick={() => {
                hub.setChannels((all) => all.filter((c) => c.id !== channel.id))
                onBack()
              }}
            >
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Frame>
  )
}

function DeleteInstancePage({
  crumb,
  instance,
  hub,
  onBack,
}: {
  crumb: string
  instance: Removed
  hub: Hub
  onBack: () => void
}) {
  const [typed, setTyped] = useState("")
  const { busy, run } = useBusy(1400)
  return (
    <Frame
      title={`Delete ${instance.name} permanently`}
      crumb={crumb}
      onBack={onBack}
      footer={
        <>
          <Button variant="ghost" onClick={onBack} disabled={busy !== undefined}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={busy !== undefined || typed !== instance.name}
            onClick={() =>
              run("delete", () => {
                hub.setRemoved((all) => all.filter((r) => r.id !== instance.id))
                onBack()
              })
            }
          >
            {busy !== undefined && <Spinner data-icon="inline-start" />}
            Delete permanently
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">
        The hub deletes these directories and volumes, and the deletion can't be undone.
      </p>
      <Group>
        <div className="p-4">
          <DeletionPreview instance={instance} typed={typed} onTyped={setTyped} />
        </div>
      </Group>
    </Frame>
  )
}
