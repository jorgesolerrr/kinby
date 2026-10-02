import type {
  Client,
  Clock,
  InstanceClient,
  InstanceStatusResult,
  InstanceSummary,
  PromptName,
  RecreateReason,
} from "@kinby/contract"
import { Fragment, type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { ManifestSection } from "@/components/manifest-section"
import { PackageConfigSection } from "@/components/package-config-section"
import { PackageSection } from "@/components/package-section"
import { PermissionsSection } from "@/components/permissions-section"
import { RecreateNotice } from "@/components/recreate-notice"
import { RoutinesSection } from "@/components/routines-section"
import { SecretsSection } from "@/components/secrets-section"
import { SkillsSection } from "@/components/skills-section"
import { ToolsSection } from "@/components/tools-section"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { usePolled } from "@/hooks/use-polled"
import { lastChanged } from "@/lib/config-changes"
import { OlderCore } from "@/lib/older-core"
import { retried } from "@/lib/operation"
import { type OpenedPrompt, openPrompt, PROMPT_FILES, savePrompt } from "@/lib/prompts"
import { leaveLink, linkedPackage, linkedRoutine } from "@/lib/selection"
import {
  BoxIcon,
  CpuIcon,
  FileTextIcon,
  KeyRoundIcon,
  type LucideIcon,
  NotebookPenIcon,
  PackageIcon,
  RepeatIcon,
  ShieldIcon,
  SparklesIcon,
  WrenchIcon,
} from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/**
 * What a section may use: the instance and its status, the hub, a way to list the instances again,
 * and the other sections.
 */
interface Panel {
  client: Caller
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  /** Undefined until the hub answers. */
  status: InstanceStatusResult | undefined
  /** Read the status again, after a change that may add a recreate reason. */
  statusChanged: () => void
  /** List the instances again. */
  onChanged: () => void
  /** Open another section by its label. */
  open: (label: string) => void
  /** The routine a link opened the panel on, until another section is opened. */
  routine: string | undefined
}

/** One section of the panel. A section without `render` is not built yet. */
interface Section {
  label: string
  hint: string
  icon: LucideIcon
  render?: (panel: Panel) => ReactNode
  /** The recreate reason a change in this section leaves. */
  reason?: RecreateReason
}

const PACKAGE = "Package and version"
const ROUTINES = "Routines"

const GROUPS: { label: string; sections: Section[] }[] = [
  {
    label: "Behavior",
    sections: [
      {
        label: "Behavior prompt",
        hint: "SYSTEM.md, how the agent acts",
        icon: FileTextIcon,
        render: ({ client, clock }) => (
          <PromptSection client={client} clock={clock} name="behavior" />
        ),
      },
      {
        label: "Recap prompt",
        hint: "RECAP.md, what a recap looks at",
        icon: NotebookPenIcon,
        render: ({ client, clock }) => <PromptSection client={client} clock={clock} name="recap" />,
      },
      {
        label: "Permissions",
        hint: "ceiling, mode, tool rules, shell patterns",
        icon: ShieldIcon,
        render: ({ client, clock }) => <PermissionsSection client={client} clock={clock} />,
      },
      {
        label: "Manifest",
        hint: "models, budgets, timezone",
        icon: CpuIcon,
        render: ({ client, clock }) => <ManifestSection client={client} clock={clock} />,
      },
    ],
  },
  {
    label: "Capabilities",
    sections: [
      {
        label: ROUTINES,
        hint: "schedules, signals, next firing",
        icon: RepeatIcon,
        render: ({ client, clock, routine }) => (
          <RoutinesSection client={client} clock={clock} opened={routine} />
        ),
      },
      {
        label: "Skills",
        hint: "instance, package, workspace",
        icon: SparklesIcon,
        render: ({ client, clock }) => <SkillsSection client={client} clock={clock} />,
      },
      {
        label: "Tools",
        hint: "what the instance can do",
        icon: WrenchIcon,
        render: ({ client, clock, open }) => (
          <ToolsSection
            client={client}
            clock={clock}
            onOpenPermissions={available(PERMISSIONS) ? () => open(PERMISSIONS) : undefined}
          />
        ),
      },
    ],
  },
  {
    label: "Instance",
    sections: [
      {
        label: "Secrets and login",
        hint: "write-only values, sign-in",
        icon: KeyRoundIcon,
        reason: "secrets",
        render: ({ caller, clock, instance, status, statusChanged }) => (
          <SecretsSection
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
            setup={status?.setup}
            onChanged={statusChanged}
          />
        ),
      },
      {
        label: "Package config",
        hint: "the package's own settings",
        icon: BoxIcon,
        reason: "package_config",
        render: ({ client, clock, statusChanged }) => (
          <PackageConfigSection client={client} clock={clock} onSaved={statusChanged} />
        ),
      },
      {
        label: PACKAGE,
        hint: "template, installed, update",
        icon: PackageIcon,
        render: ({ caller, clock, instance, onChanged }) => (
          <PackageSection caller={caller} clock={clock} instance={instance} onChanged={onChanged} />
        ),
      },
    ],
  },
]

const SECTIONS = GROUPS.flatMap((group) => group.sections)
const PERMISSIONS = "Permissions"

function available(label: string): boolean {
  return SECTIONS.some((section) => section.label === label && section.render !== undefined)
}

/** When a saved prompt takes effect. */
const APPLIES: Record<PromptName, string> = {
  behavior: "Saved. It applies at the next turn.",
  recap: "Saved. It applies at the next recap.",
}

/** How often the panel reads the instance's status, for a change the hub or another tab made. */
const STATUS_INTERVAL_MS = 30_000

/**
 * The instance's config: its sections grouped in a left column, the selected one on the right,
 * and above it one notice for every change that waits on a recreate. A section that needs the user
 * says why in place of its hint. `client` reaches the instance and `caller` the hub, and
 * `onChanged` lists the instances again.
 */
export function ConfigPanel({
  client,
  caller,
  clock,
  instance,
  onChanged,
}: {
  client: Caller
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onChanged: () => void
}) {
  const [routine, setRoutine] = useState(linkedRoutine)
  const [selected, setSelected] = useState(() => linkedSection(routine))
  const instanceId = instance.instance_id
  const open = (label: string) => {
    setSelected(label)
    setRoutine(undefined)
    leaveLink(instanceId)
  }
  const section = SECTIONS.find((candidate) => candidate.label === selected)
  const read = useCallback(
    () => caller.call("instance.status", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const [status, readAgain] = usePolled(read, clock, true, STATUS_INTERVAL_MS)
  const statusChanged = useCallback(() => void readAgain(), [readAgain])
  const reasons = status?.recreate_reasons ?? []
  const needs = needsOf(instance, reasons)
  const panel: Panel = {
    client,
    caller,
    clock,
    instance,
    status,
    statusChanged,
    onChanged,
    open,
    routine,
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <nav
        aria-label="Config sections"
        className="flex shrink-0 flex-col gap-4 overflow-y-auto border-b p-3 md:w-72 md:border-r md:border-b-0"
      >
        {GROUPS.map((group) => (
          <SectionGroup
            key={group.label}
            label={group.label}
            sections={group.sections}
            needs={needs}
            selected={selected}
            onSelect={open}
          />
        ))}
      </nav>
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
        <RecreateNotice
          caller={caller}
          clock={clock}
          instanceId={instanceId}
          reasons={reasons}
          onRecreated={statusChanged}
        />
        <div>
          <h1 className="text-xl font-semibold">{section?.label}</h1>
          <p className="text-sm text-muted-foreground">{section?.hint}</p>
        </div>
        <Separator />
        <OlderCore value={behind(instance) ? () => open(PACKAGE) : undefined}>
          <Fragment key={selected}>{section?.render?.(panel)}</Fragment>
        </OlderCore>
      </main>
    </div>
  )
}

/** The section a link opens the panel on: the routines for a linked routine, or the first one. */
function linkedSection(routine: string | undefined): string {
  if (routine !== undefined) return ROUTINES
  if (linkedPackage()) return PACKAGE
  return "Behavior prompt"
}

/**
 * Why each section needs the user, by its label: a change that waits on a recreate, or an
 * installed package behind the hub's.
 */
function needsOf(
  instance: InstanceSummary,
  reasons: RecreateReason[],
): Partial<Record<string, string>> {
  const needs: Partial<Record<string, string>> = {}
  for (const { label, reason } of SECTIONS) {
    if (reason !== undefined && reasons.includes(reason)) needs[label] = "Recreate to apply"
  }
  if (behind(instance)) needs[PACKAGE] = "behind the hub"
  return needs
}

function behind(instance: InstanceSummary): boolean {
  return instance.notices.some((notice) => notice.code === "revision_behind")
}

/** One group of the left column, as a list named for the group. A need replaces a hint. */
function SectionGroup({
  label,
  sections,
  needs,
  selected,
  onSelect,
}: {
  label: string
  sections: Section[]
  needs: Partial<Record<string, string>>
  selected: string
  onSelect: (label: string) => void
}) {
  const labelId = useId()
  return (
    <div className="flex flex-col gap-1">
      <span id={labelId} className="px-3 text-xs font-medium text-muted-foreground">
        {label}
      </span>
      <ItemGroup aria-labelledby={labelId}>
        {sections.map(({ label, hint, icon: Icon, render }) => (
          <li key={label} className="list-none">
            <Item
              size="xs"
              variant={label === selected ? "muted" : "default"}
              className="text-left"
              render={
                <button
                  type="button"
                  aria-label={label}
                  aria-current={label === selected || undefined}
                  disabled={render === undefined}
                  onClick={() => onSelect(label)}
                />
              }
            >
              <ItemMedia variant="icon">
                <Icon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{label}</ItemTitle>
                <ItemDescription>
                  {render === undefined ? "Not available yet" : (needs[label] ?? hint)}
                </ItemDescription>
              </ItemContent>
            </Item>
          </li>
        ))}
      </ItemGroup>
    </div>
  )
}

/**
 * One prompt as text. A save carries the hash of what was read, so a save over a change made
 * since is refused, and "Load theirs" reads the prompt again.
 */
function PromptSection({
  client,
  clock,
  name,
}: {
  client: Caller
  clock: Clock
  name: PromptName
}) {
  const id = useId()
  const pacing = usePace(clock)
  const file = PROMPT_FILES[name]
  const [opened, setOpened] = useState<OpenedPrompt>()
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [failure, setFailure] = useState<unknown>()

  const show = useCallback((read: OpenedPrompt) => {
    setOpened(read)
    setDraft(read.prompt.content)
  }, [])
  const load = useCallback(
    () =>
      retried(() => openPrompt(client, name), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, name, pacing, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await savePrompt(client, name, draft, opened.prompt.hash)
      if (saved.state === "saved") show(saved.opened)
      setNotice(saved.state)
    } catch (error) {
      setFailure(error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {notice === "stale" && <StaleAlert file={file} onLoad={() => void load()} />}
      {failure !== undefined && <Failure error={failure} />}
      <Field>
        <div className="flex items-center gap-2">
          <FieldLabel htmlFor={id}>{file}</FieldLabel>
          {opened.prompt.default && <Badge variant="secondary">Shipped default</Badge>}
        </div>
        {opened.prompt.default && (
          <FieldDescription>
            The instance has no {file}, so this is the text kinby ships. Saving writes the file.
          </FieldDescription>
        )}
        <Textarea
          id={id}
          className="min-h-72"
          value={draft}
          readOnly={saving}
          onChange={(event) => {
            setDraft(event.target.value)
            if (notice === "saved") setNotice(undefined)
          }}
        />
        <FieldDescription>{lastChanged(opened.lastChange, opened.prompt.hash)}</FieldDescription>
      </Field>
      <div className="flex items-center gap-3">
        <Button disabled={saving || draft === opened.prompt.content} onClick={() => void save()}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">{APPLIES[name]}</span>
        )}
      </div>
    </div>
  )
}
