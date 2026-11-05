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
import { CoreSection } from "@/components/core-section"
import { ManifestSection } from "@/components/manifest-section"
import { PermissionsSection } from "@/components/permissions-section"
import { RecreateNotice } from "@/components/recreate-notice"
import { SectionFrame } from "@/components/section-frame"
import { SecretsSection } from "@/components/secrets-section"
import { SkillsSection } from "@/components/skills-section"
import { ToolsSection } from "@/components/tools-section"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useIsMobile } from "@/hooks/use-mobile"
import { usePace } from "@/hooks/use-pace"
import { usePolled } from "@/hooks/use-polled"
import type { Update } from "@/hooks/use-update"
import { lastChanged } from "@/lib/config-changes"
import { behind, OlderCore } from "@/lib/older-core"
import { type Pace, pace, retried } from "@/lib/operation"
import { type OpenedPrompt, openPrompt, PROMPT_FILES, savePrompt } from "@/lib/prompts"
import { type ConfigLink, leaveLink, linkedSection } from "@/lib/selection"

type Caller = Pick<InstanceClient, "call">

/**
 * What a section may use: the instance and its status, the hub, the instance's update, and the
 * other sections.
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
  /** The instance's one update, lent by its header. */
  update: Update
  /** Open another section by its label. */
  open: (label: string) => void
}

/** One section of the panel. A section without `render` is not built yet. */
interface Section {
  label: string
  hint: string
  render?: (panel: Panel) => ReactNode
  /** The recreate reason a change in this section leaves. */
  reason?: RecreateReason
}

const CORE = "Core version"

const GROUPS: { label: string; sections: Section[] }[] = [
  {
    label: "Behavior",
    sections: [
      {
        label: "Behavior prompt",
        hint: "SYSTEM.md, how the agent acts",
        render: ({ client, clock }) => (
          <PromptSection client={client} clock={clock} name="behavior" />
        ),
      },
      {
        label: "Recap prompt",
        hint: "RECAP.md, what a recap looks at",
        render: ({ client, clock }) => <PromptSection client={client} clock={clock} name="recap" />,
      },
      {
        label: "Permissions",
        hint: "ceiling, mode, tool rules, shell patterns",
        render: ({ client, clock }) => <PermissionsSection client={client} clock={clock} />,
      },
      {
        label: "Manifest",
        hint: "models, budgets, timezone",
        render: ({ client, clock }) => <ManifestSection client={client} clock={clock} />,
      },
    ],
  },
  {
    label: "Capabilities",
    sections: [
      {
        label: "Skills",
        hint: "library picks, instance, kinby, workspace",
        render: ({ client, caller, clock, instance }) => (
          <SkillsSection
            client={client}
            caller={caller}
            clock={clock}
            instanceId={instance.instance_id}
          />
        ),
      },
      {
        label: "Tools",
        hint: "what the instance can do",
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
        label: CORE,
        hint: "the kinby it runs, update",
        render: ({ instance, update }) => <CoreSection instance={instance} update={update} />,
      },
    ],
  },
]

const SECTIONS = GROUPS.flatMap((group) => group.sections)
const PERMISSIONS = "Permissions"

/** The section each link opens, by its label. */
const LINKED: Record<ConfigLink, string> = { core: CORE, skills: "Skills" }

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
 * The instance's config: its sections grouped in a left column, or a select on a phone, the
 * selected one beside it, and above it one notice for every change that waits on a recreate. A dot
 * marks a section that needs a look. `client` reaches the instance and `caller` the hub, and
 * `update` is the one the instance header lends.
 */
export function ConfigPanel({
  client,
  caller,
  clock,
  instance,
  update,
}: {
  client: Caller
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  update: Update
}) {
  const [selected, setSelected] = useState(() => {
    const linked = linkedSection()
    return linked === undefined ? "Behavior prompt" : LINKED[linked]
  })
  const instanceId = instance.instance_id
  const open = (label: string) => {
    setSelected(label)
    leaveLink(instanceId)
  }
  const section = SECTIONS.find((candidate) => candidate.label === selected)
  const read = useCallback(
    () => caller.call("instance.status", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const [status, readAgain] = usePolled(read, clock, true, STATUS_INTERVAL_MS)
  const statusChanged = useCallback(() => void readAgain(), [readAgain])
  const isMobile = useIsMobile()
  const reasons = status?.recreate_reasons ?? []
  const needs = needsOf(instance, reasons)
  const panel: Panel = {
    client,
    caller,
    clock,
    instance,
    status,
    statusChanged,
    update,
    open,
  }
  return (
    <div className="flex flex-col gap-6 py-6 max-md:py-4 md:flex-row md:gap-8">
      {isMobile ? (
        <SectionPicker needs={needs} selected={selected} onSelect={open} />
      ) : (
        <nav aria-label="Config sections" className="flex w-52 shrink-0 flex-col gap-4">
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
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <RecreateNotice
          caller={caller}
          clock={clock}
          instanceId={instanceId}
          reasons={reasons}
          onRecreated={statusChanged}
        />
        {section !== undefined && (
          <SectionFrame variant="page" title={section.label} lead={section.hint}>
            <OlderCore value={behind(instance) ? () => open(CORE) : undefined}>
              <Fragment key={selected}>{section.render?.(panel)}</Fragment>
            </OlderCore>
          </SectionFrame>
        )}
      </div>
    </div>
  )
}

/**
 * Why each section needs the user, by its label: a change that waits on a recreate, a core
 * behind the hub's, files behind the factory's, or an environment without its hub route.
 */
function needsOf(
  instance: InstanceSummary,
  reasons: RecreateReason[],
): Partial<Record<string, string>> {
  const needs: Partial<Record<string, string>> = {}
  for (const { label, reason } of SECTIONS) {
    if (reason !== undefined && reasons.includes(reason)) needs[label] = "Recreate to apply"
  }
  const codes = new Set(instance.notices.map((notice) => notice.code))
  if (codes.has("revision_behind")) needs[CORE] = "behind the hub"
  else if (codes.has("template_behind")) needs[CORE] = "behind the factory"
  else if (codes.has("hub_route_missing")) needs[CORE] = "no hub route"
  return needs
}

/** One group of the left column, its sections as buttons. A dot marks a section that needs a look. */
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
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 px-3 text-xs font-medium text-muted-foreground">{label}</legend>
      {sections.map(({ label, render }) => (
        <SectionButton
          key={label}
          label={label}
          need={needs[label]}
          current={label === selected}
          disabled={render === undefined}
          onClick={() => onSelect(label)}
        />
      ))}
    </fieldset>
  )
}

function SectionButton({
  label,
  need,
  current,
  disabled,
  onClick,
}: {
  label: string
  need: string | undefined
  current: boolean
  disabled: boolean
  onClick: () => void
}) {
  const needId = useId()
  return (
    <Button
      variant="nav"
      aria-label={label}
      aria-describedby={need === undefined ? undefined : needId}
      aria-current={current ? "page" : undefined}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
      {need !== undefined && <NeedDot id={needId} need={need} />}
    </Button>
  )
}

function NeedDot({ id, need }: { id?: string; need: string }) {
  return (
    <span id={id} className="ml-auto size-2 shrink-0 self-center rounded-full bg-ember">
      <span className="sr-only">{need}</span>
    </span>
  )
}

/** The sections as a select, which takes the column's place on a phone. */
function SectionPicker({
  needs,
  selected,
  onSelect,
}: {
  needs: Partial<Record<string, string>>
  selected: string
  onSelect: (label: string) => void
}) {
  return (
    <Select
      items={SECTIONS.map(({ label }) => ({ value: label, label }))}
      value={selected}
      onValueChange={(picked) => {
        if (typeof picked === "string") onSelect(picked)
      }}
    >
      <SelectTrigger aria-label="Config section" className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {GROUPS.map((group) => (
          <SelectGroup key={group.label}>
            <SelectLabel>{group.label}</SelectLabel>
            {group.sections.map(({ label, render }) => (
              <SelectItem key={label} value={label} disabled={render === undefined}>
                {label}
                {needs[label] !== undefined && <NeedDot need={needs[label]} />}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
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
    (pacing: Pace) =>
      retried(() => openPrompt(client, name), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, name, show],
  )
  useEffect(() => {
    const pacing = pace(clock)
    void load(pacing)
    return pacing.stop
  }, [clock, load])

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
      {notice === "stale" && <StaleAlert file={file} onLoad={() => void load(pacing)} />}
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
