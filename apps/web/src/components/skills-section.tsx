import type {
  Clock,
  InstanceClient,
  SkillListResult,
  SkillSummary,
  SkillTier,
} from "@kinby/contract"
import { cn } from "cn"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert, Warnings } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { lastChanged } from "@/lib/config-changes"
import { retried } from "@/lib/operation"
import {
  byName,
  type OpenedSkill,
  openSkill,
  removeSkill,
  skillFile,
  writeSkill,
} from "@/lib/skills"
import { PlusIcon, SparklesIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** The skill open below the list, or the form for a new one. */
type Selection = { name: string; tier: SkillTier } | "new"

/**
 * Every skill in every tier, each name's shadowed skills greyed under the one the model reads.
 * Any skill opens read-only; an instance skill opens to edit.
 */
export function SkillsSection({ client, clock }: { client: Caller; clock: Clock }) {
  const pacing = usePace(clock)
  const [listed, setListed] = useState<SkillListResult>()
  const [failure, setFailure] = useState<unknown>()
  const [selection, setSelection] = useState<Selection>()

  const load = useCallback(
    () =>
      retried(() => client.call("skill.list", {}), pacing).then(
        (result) => {
          setListed(result)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, pacing],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (listed === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  const groups = byName(listed.skills)
  const openInstanceSkill = (name: string) => {
    setSelection({ name, tier: "instance" })
    void load()
  }
  const removed = (name: string, after: SkillListResult) => {
    setListed(after)
    const winner = byName(after.skills).find((group) => group.winner.name === name)?.winner
    setSelection(winner && { name, tier: winner.tier })
  }

  return (
    <div className="flex flex-col gap-4">
      {failure !== undefined && <Failure error={failure} />}
      <Warnings warnings={listed.warnings} />
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          The model reads the first skill of each name: instance, then package, then workspace.
        </p>
        <Button variant="outline" size="sm" onClick={() => setSelection("new")}>
          <PlusIcon data-icon="inline-start" />
          New skill
        </Button>
      </div>
      {groups.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <SparklesIcon />
            </EmptyMedia>
            <EmptyTitle>No skills</EmptyTitle>
            <EmptyDescription>
              Neither the instance, its packages, nor its workspace has a skill.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ItemGroup aria-label="Skills">
          {groups.flatMap(({ winner, shadowed }) =>
            [winner, ...shadowed].map((skill) => (
              <li
                key={`${skill.name} ${skill.tier}`}
                className={cn("list-none", skill.shadowed_by && "pl-6")}
              >
                <SkillItem
                  skill={skill}
                  selected={
                    selection !== "new" &&
                    selection?.name === skill.name &&
                    selection.tier === skill.tier
                  }
                  onSelect={() => setSelection({ name: skill.name, tier: skill.tier })}
                />
              </li>
            )),
          )}
        </ItemGroup>
      )}
      {selection !== undefined && <Separator />}
      {selection === "new" && <NewSkill client={client} onCreated={openInstanceSkill} />}
      {selection !== undefined && selection !== "new" && (
        <SkillView
          key={`${selection.name} ${selection.tier}`}
          client={client}
          clock={clock}
          summary={listed.skills.find(
            (skill) => skill.name === selection.name && skill.tier === selection.tier,
          )}
          name={selection.name}
          tier={selection.tier}
          hides={groups.some(
            (group) => group.winner.name === selection.name && group.shadowed.length > 0,
          )}
          onCustomized={openInstanceSkill}
          onSaved={() => void load()}
          onRemoved={removed}
        />
      )}
    </div>
  )
}

const TIERS: Record<SkillTier, string> = {
  instance: "Instance",
  package: "Package",
  workspace: "Workspace",
}

function SkillItem({
  skill,
  selected,
  onSelect,
}: {
  skill: SkillSummary
  selected: boolean
  onSelect: () => void
}) {
  let variant: "default" | "muted" | "outline" = skill.shadowed_by ? "muted" : "default"
  if (selected) variant = "outline"
  return (
    <Item
      size="sm"
      variant={variant}
      render={
        <button
          type="button"
          aria-label={`${skill.name}, ${skill.tier} skill`}
          aria-current={selected || undefined}
          onClick={onSelect}
        />
      }
    >
      <ItemContent>
        <ItemTitle>
          {skill.name}
          <Badge variant={skill.tier === "instance" ? "default" : "secondary"}>
            {TIERS[skill.tier]}
          </Badge>
        </ItemTitle>
        <ItemDescription>
          {skill.shadowed_by ? `Hidden by the ${skill.shadowed_by} skill` : skill.description}
        </ItemDescription>
      </ItemContent>
      {skill.tier === "package" && (
        <ItemActions>
          <span className="text-xs text-muted-foreground">{skill.source}</span>
        </ItemActions>
      )}
    </Item>
  )
}

/** One skill: read-only below the instance tier, editable in it. */
function SkillView({
  client,
  clock,
  summary,
  name,
  tier,
  hides,
  onCustomized,
  onSaved,
  onRemoved,
}: {
  client: Caller
  clock: Clock
  summary: SkillSummary | undefined
  name: string
  tier: SkillTier
  /** Whether this skill hides one in a lower tier, so deleting it brings that one back. */
  hides: boolean
  onCustomized: (name: string) => void
  onSaved: () => void
  onRemoved: (name: string, after: SkillListResult) => void
}) {
  const id = useId()
  const pacing = usePace(clock)
  const [opened, setOpened] = useState<OpenedSkill>()
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [invalid, setInvalid] = useState<string>()
  const [failure, setFailure] = useState<unknown>()

  const show = useCallback((read: OpenedSkill) => {
    setOpened(read)
    setDraft(read.skill.content)
  }, [])
  const load = useCallback(
    () =>
      retried(() => openSkill(client, name, tier), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setInvalid(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, name, tier, pacing, show],
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
  const editable = tier === "instance"
  const act = async (action: () => Promise<void>) => {
    setBusy(true)
    setFailure(undefined)
    try {
      await action()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }
  const save = () =>
    act(async () => {
      const written = await writeSkill(client, name, draft, opened.skill.hash)
      if (written.state === "saved") {
        show(written.opened)
        onSaved()
      }
      setInvalid(written.state === "invalid" ? Object.values(written.fields).join(" ") : undefined)
      setNotice(written.state === "invalid" ? undefined : written.state)
    })
  const customize = () =>
    act(async () => {
      await client.call("skill.customize", { name })
      onCustomized(name)
    })
  const remove = () =>
    act(async () => {
      const removed = await removeSkill(client, name, opened.skill.hash)
      if (removed.state === "removed") onRemoved(name, removed.listed)
      else if (removed.state === "stale") setNotice("stale")
      else setInvalid(Object.values(removed.fields).join(" "))
    })

  return (
    <div className="flex flex-col gap-3">
      {notice === "stale" && <StaleAlert file={skillFile(name)} onLoad={() => void load()} />}
      {failure !== undefined && <Failure error={failure} />}
      <Field data-invalid={invalid !== undefined || undefined}>
        <div className="flex items-center gap-2">
          <FieldLabel htmlFor={id}>SKILL.md</FieldLabel>
          <Badge variant={editable ? "default" : "secondary"}>{TIERS[tier]}</Badge>
          {summary !== undefined && tier === "package" && (
            <span className="text-xs text-muted-foreground">{summary.source}</span>
          )}
        </div>
        {!editable && (
          <FieldDescription>
            {summary?.shadowed_by
              ? `The ${summary.shadowed_by} skill hides this one, so the model reads that.`
              : `A ${tier} skill is read-only. Customize it to edit a copy in the instance.`}
          </FieldDescription>
        )}
        <Textarea
          id={id}
          className="min-h-72"
          value={draft}
          readOnly={!editable || busy}
          aria-invalid={invalid !== undefined || undefined}
          onChange={(event) => {
            setDraft(event.target.value)
            if (notice === "saved") setNotice(undefined)
          }}
        />
        {invalid !== undefined && <FieldError>{invalid}</FieldError>}
        {opened.skill.files.length > 0 && (
          <FieldDescription>Other files: {opened.skill.files.join(", ")}</FieldDescription>
        )}
        {editable && <FieldDescription>{lastChanged(opened.lastChange)}</FieldDescription>}
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        {editable && (
          <Button disabled={busy || draft === opened.skill.content} onClick={() => void save()}>
            {busy && <Spinner data-icon="inline-start" />}
            Save
          </Button>
        )}
        {editable && (
          <Button variant="outline" disabled={busy} onClick={() => void remove()}>
            {hides ? "Remove customization" : "Delete"}
          </Button>
        )}
        {!editable && summary?.shadowed_by === null && (
          <Button disabled={busy} onClick={() => void customize()}>
            {busy && <Spinner data-icon="inline-start" />}
            Customize
          </Button>
        )}
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">Saved. It applies at the next turn.</span>
        )}
      </div>
    </div>
  )
}

/** A new instance skill, written from its name, description, and instructions. */
function NewSkill({ client, onCreated }: { client: Caller; onCreated: (name: string) => void }) {
  const id = useId()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()

  const create = async () => {
    setBusy(true)
    setFailure(undefined)
    try {
      const content = `---\nname: ${name}\ndescription: ${description}\n---\n${instructions}\n`
      const written = await writeSkill(client, name, content, null)
      if (written.state === "saved") onCreated(name)
      else if (written.state === "stale") {
        setRefusal({ name: `The instance already has a skill named ${name}.` })
      } else setRefusal(written.fields)
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {failure !== undefined && <Failure error={failure} />}
      <FieldGroup>
        <Field data-invalid={refusal.name !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
          <Input
            id={`${id}-name`}
            value={name}
            aria-invalid={refusal.name !== undefined || undefined}
            onChange={(event) => setName(event.target.value)}
          />
          {refusal.name !== undefined && <FieldError>{refusal.name}</FieldError>}
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
          <Input
            id={`${id}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <FieldDescription>
            The model sees it in the skill list and reads on from there.
          </FieldDescription>
        </Field>
        <Field data-invalid={refusal.content !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-instructions`}>Instructions</FieldLabel>
          <Textarea
            id={`${id}-instructions`}
            className="min-h-48"
            value={instructions}
            aria-invalid={refusal.content !== undefined || undefined}
            onChange={(event) => setInstructions(event.target.value)}
          />
          {refusal.content !== undefined && <FieldError>{refusal.content}</FieldError>}
        </Field>
      </FieldGroup>
      <div>
        <Button disabled={busy || name === "" || description === ""} onClick={() => void create()}>
          {busy && <Spinner data-icon="inline-start" />}
          Create
        </Button>
      </div>
    </div>
  )
}
