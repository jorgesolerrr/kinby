import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  LibraryEntry,
  LibraryGitPreviewResult,
  LibrarySkillCheckResult,
  LibrarySource,
  NewerVersion,
  PreviewedSkill,
  SkillResult,
} from "@kinby/contract"
import { CircleXIcon, Trash2Icon } from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { Failure, Refusal } from "@/components/config-alerts"
import { NewSkill, type WriteNew } from "@/components/new-skill"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useHubCall } from "@/hooks/use-hub-call"
import { usePace } from "@/hooks/use-pace"
import { type Pace, pace, retried } from "@/lib/operation"

type Caller = Pick<Client, "call">

/** Where a skill came from, as its unfolded row says it. */
const ORIGINS: Record<LibrarySource, string> = {
  shipped: "Shipped with kinby",
  written: "Written here",
  git: "Added from git",
}

/**
 * A library skill unfolded: a shipped or git skill's source and newer version to move to, the
 * instances that picked it, its `SKILL.md` to edit, then where it came from, Delete and Save. An
 * instance that picked it keeps its copy.
 */
export function SkillEntry({
  client,
  clock,
  entry,
  newer,
  usedBy,
  onSaved,
  onChecked,
  onMoved,
  onDeleted,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
  /** Whether the list or a check found a newer version. */
  newer: boolean
  usedBy: ReactNode
  onSaved: () => void
  onChecked: (newer: boolean) => void
  onMoved: () => void
  onDeleted: () => void
}) {
  const { name } = entry
  const id = useId()
  const pacing = usePace(clock)
  const [opened, setOpened] = useState<SkillResult>()
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [invalid, setInvalid] = useState<string>()
  const [failure, setFailure] = useState<unknown>()

  const show = useCallback((read: SkillResult) => {
    setOpened(read)
    setDraft(read.content)
  }, [])
  const load = useCallback(
    (pacing: Pace) =>
      retried(() => client.call("library.skill.read", { name }), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setInvalid(undefined)
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
  const version = useNewerVersion({
    client,
    name,
    edited: entry.edited === true,
    onChecked,
    onMoved: () => {
      void load(pacing)
      onMoved()
    },
  })

  const act = async (action: () => Promise<void>) => {
    setBusy(true)
    setNotice(undefined)
    setInvalid(undefined)
    setFailure(undefined)
    try {
      await action()
    } catch (error) {
      if (error instanceof CallError && error.code === "STALE") setNotice("stale")
      else if (error instanceof CallError && error.code === "INVALID_ARGUMENT") {
        setInvalid(Object.values(error.fields).join(" "))
      } else setFailure(error)
    } finally {
      setBusy(false)
    }
  }
  const save = (read: SkillResult) =>
    act(async () => {
      show(await client.call("library.skill.write", { name, content: draft, hash: read.hash }))
      // A move goes over the version the check read, which the save just replaced.
      version.forget()
      setNotice("saved")
      onSaved()
    })
  const remove = (read: SkillResult) =>
    act(async () => {
      await client.call("library.skill.delete", { name, hash: read.hash })
      onDeleted()
    })

  return (
    <>
      {entry.source !== "written" && (
        <NewerVersionNotice
          entry={entry}
          newer={newer}
          version={version}
          unsaved={opened !== undefined && draft !== opened.content}
        />
      )}
      {usedBy}
      {opened === undefined ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <Field data-invalid={invalid !== undefined || undefined}>
          <FieldLabel htmlFor={id}>SKILL.md</FieldLabel>
          <Textarea
            id={id}
            className="min-h-72"
            value={draft}
            readOnly={busy}
            aria-invalid={invalid !== undefined || undefined}
            onChange={(event) => {
              setDraft(event.target.value)
              if (notice === "saved") setNotice(undefined)
            }}
          />
          {invalid !== undefined && <FieldError>{invalid}</FieldError>}
          {opened.files.length > 0 && (
            <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
          )}
        </Field>
      )}
      {notice === "saved" && (
        <p className="text-sm text-muted-foreground">
          Saved. Instances take it when they pick it or update.
        </p>
      )}
      {notice === "stale" && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>Changed since you opened it</AlertTitle>
          <AlertDescription>
            {name} was saved since you opened it. Load it again to see it. Your edits here are
            dropped.
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={() => void load(pacing)}>
              Load again
            </Button>
          </AlertAction>
        </Alert>
      )}
      {failure !== undefined && <Failure error={failure} />}
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-muted-foreground">
          {ORIGINS[entry.source]}
          {entry.edited && " · edited here"}
        </p>
        <AlertDialog>
          <AlertDialogTrigger
            render={
              <Button variant="destructive" size="sm" disabled={busy || opened === undefined} />
            }
          >
            <Trash2Icon data-icon="inline-start" />
            Delete
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                Instances that picked it keep their copy.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => opened !== undefined && void remove(opened)}
              >
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <Button
          size="sm"
          disabled={busy || opened === undefined || draft === opened.content}
          onClick={() => opened !== undefined && void save(opened)}
        >
          {busy && <Spinner data-icon="inline-start" />}
          Save
        </Button>
      </div>
    </>
  )
}

/**
 * Check a shipped or git skill for a newer version, and move the library's skill to the one a
 * check showed. A skill the user edited moves over the edits, once they agree to it. `forget`
 * drops the check, which a save outdates.
 */
function useNewerVersion({
  client,
  name,
  edited,
  onChecked,
  onMoved,
}: {
  client: Caller
  name: string
  edited: boolean
  onChecked: (newer: boolean) => void
  onMoved: () => void
}) {
  const [checked, setChecked] = useState<LibrarySkillCheckResult>()
  const { busy, refusal, failure, ask } = useHubCall()

  const check = () =>
    ask(async () => {
      const result = await client.call("library.skill.check", { name })
      setChecked(result)
      onChecked(result.newer !== null)
    })
  const move = (newer: NewerVersion) =>
    ask(async () => {
      await client.call("library.skill.move", {
        name,
        hash: newer.hash,
        commit: newer.commit,
        overwrite: edited,
      })
      setChecked(undefined)
      onMoved()
    })
  const forget = () => setChecked(undefined)
  return { checked, busy, refusal, failure, check, move, forget }
}

/**
 * A git skill's source and the check for a newer version, or that a newer version is out, with its
 * diff once checked and the move to it. A skill with unsaved edits moves only once they are saved.
 */
function NewerVersionNotice({
  entry,
  newer: known,
  version,
  unsaved,
}: {
  entry: LibraryEntry
  newer: boolean
  version: ReturnType<typeof useNewerVersion>
  unsaved: boolean
}) {
  const id = useId()
  const { name } = entry
  const newer = version.checked?.newer
  return (
    <section className="flex flex-col gap-3" aria-label="Newer version">
      {version.failure !== undefined && <Failure error={version.failure} />}
      {version.refusal !== undefined && <Refusal reason={version.refusal} />}
      {newer != null ? (
        <>
          <Alert>
            <AlertTitle>A newer version is out</AlertTitle>
            <AlertDescription>
              <p>
                {newer.commit === null ? "The version this kinby ships." : `Commit ${newer.commit}`}
              </p>
              {unsaved && <p>Save your edits to SKILL.md before moving it.</p>}
            </AlertDescription>
            <AlertAction>
              {entry.edited === true ? (
                <AlertDialog>
                  <AlertDialogTrigger
                    render={<Button size="sm" disabled={version.busy || unsaved} />}
                  >
                    Move to this version
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Overwrite your edits to {name}?</AlertDialogTitle>
                      <AlertDialogDescription>
                        The newer version replaces the library&apos;s copy you edited. Instances
                        that picked it keep theirs.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        variant="destructive"
                        onClick={() => void version.move(newer)}
                      >
                        Overwrite
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              ) : (
                <Button
                  size="sm"
                  disabled={version.busy || unsaved}
                  onClick={() => void version.move(newer)}
                >
                  Move to this version
                </Button>
              )}
            </AlertAction>
          </Alert>
          <Field>
            <FieldLabel htmlFor={id}>Diff</FieldLabel>
            <Textarea id={id} className="min-h-48" value={newer.diff} readOnly />
          </Field>
        </>
      ) : known ? (
        <Alert>
          <AlertTitle>A newer version is out</AlertTitle>
          <AlertDescription>
            Read the diff, then move the library&apos;s copy to it. Instances that picked it keep
            theirs.
          </AlertDescription>
          <AlertAction>
            <Button
              size="sm"
              variant="outline"
              disabled={version.busy}
              onClick={() => void version.check()}
            >
              Read the diff
            </Button>
          </AlertAction>
        </Alert>
      ) : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
          {entry.git != null && (
            <span className="min-w-0 truncate">{`${entry.git.url} at ${entry.git.commit}`}</span>
          )}
          {version.checked?.newer === null && <span>No newer version.</span>}
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            disabled={version.busy}
            onClick={() => void version.check()}
          >
            {version.busy && <Spinner data-icon="inline-start" />}
            Check for a newer version
          </Button>
        </div>
      )}
    </section>
  )
}

/** A new skill written from its name, description and instructions, or Cancel. */
export function WriteSkill({
  client,
  onWritten,
  onCancel,
}: {
  client: Caller
  onWritten: () => void
  onCancel: () => void
}) {
  const write: WriteNew = async (name, content) => {
    try {
      await client.call("library.skill.write", { name, content, hash: null })
    } catch (error) {
      if (error instanceof CallError && error.code === "INVALID_ARGUMENT") return error.fields
      throw error
    }
    return undefined
  }
  return <NewSkill write={write} onCreated={onWritten} onCancel={onCancel} />
}

/** What the hub showed for an address. */
interface Previewed {
  address: string
  shown: LibraryGitPreviewResult
}

/**
 * Add skills from a git repository: the address, the hub's preview of the skills it holds at the
 * commit the address resolves to, then the ones to add. What lands is that commit.
 */
export function AddFromGit({
  client,
  held,
  onAdded,
  onCancel,
}: {
  client: Caller
  /** The names of the skills the library holds, which the preview offers no way to add again. */
  held: string[]
  onAdded: () => void
  onCancel: () => void
}) {
  const id = useId()
  const [address, setAddress] = useState("")
  const [preview, setPreview] = useState<Previewed>()
  const [chosen, setChosen] = useState<string[]>([])
  const { busy, refusal, failure, ask } = useHubCall()
  // The library can come to hold a chosen skill after the preview, as another tab adds it.
  const ticked = chosen.filter((name) => !held.includes(name))

  const previewAddress = (event: FormEvent) => {
    event.preventDefault()
    void ask(async () => {
      const shown = await client.call("library.git.preview", { address })
      setPreview({ address, shown })
      setChosen(shown.skills.map((skill) => skill.name).filter((name) => !held.includes(name)))
    })
  }

  const add = (previewed: Previewed, skills: string[]) => {
    const [first, ...others] = skills
    if (first === undefined) return
    void ask(async () => {
      await client.call("library.git.add", {
        address: previewed.address,
        commit: previewed.shown.commit,
        skills: [first, ...others],
      })
      onAdded()
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={previewAddress}>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor={id}>Address</FieldLabel>
            <Input
              id={id}
              value={address}
              placeholder="owner/repo"
              onChange={(event) => {
                setAddress(event.target.value)
                setPreview(undefined)
              }}
            />
            <FieldDescription>
              owner/repo, owner/repo/path or a URL, each with an optional #ref.
            </FieldDescription>
          </Field>
          <Field orientation="horizontal">
            <Button type="submit" disabled={busy || address.trim() === ""}>
              Preview
            </Button>
            <Button type="button" variant="outline" onClick={onCancel}>
              Cancel
            </Button>
          </Field>
        </FieldGroup>
      </form>
      {failure !== undefined && <Failure error={failure} />}
      {refusal !== undefined && <Refusal reason={refusal} />}
      {preview !== undefined && (
        <FieldSet>
          <FieldLegend>Skills to add</FieldLegend>
          <FieldDescription>At commit {preview.shown.commit}</FieldDescription>
          <FieldGroup>
            {preview.shown.skills.map((skill) => (
              <SkillPreview
                key={skill.name}
                skill={skill}
                held={held.includes(skill.name)}
                chosen={ticked.includes(skill.name)}
                onChosen={(on) =>
                  setChosen((names) =>
                    on ? [...names, skill.name] : names.filter((name) => name !== skill.name),
                  )
                }
              />
            ))}
          </FieldGroup>
          <Field orientation="horizontal">
            <Button disabled={busy || ticked.length === 0} onClick={() => add(preview, ticked)}>
              Add {ticked.length} {ticked.length === 1 ? "skill" : "skills"}
            </Button>
          </Field>
        </FieldSet>
      )}
    </div>
  )
}

/**
 * One skill a preview found: whether to add it, and what the model would read. A skill the library
 * already holds is greyed out and cannot be chosen.
 */
function SkillPreview({
  skill,
  held,
  chosen,
  onChosen,
}: {
  skill: PreviewedSkill
  held: boolean
  chosen: boolean
  onChosen: (chosen: boolean) => void
}) {
  const id = useId()
  return (
    <FieldGroup>
      <Field orientation="horizontal" data-disabled={held}>
        <Checkbox id={`${id}-choose`} checked={chosen} disabled={held} onCheckedChange={onChosen} />
        <FieldContent>
          <FieldLabel htmlFor={`${id}-choose`}>{skill.name}</FieldLabel>
          <FieldDescription>{skill.path}</FieldDescription>
          {held && <FieldDescription>Already in the library</FieldDescription>}
        </FieldContent>
      </Field>
      <Field>
        <FieldLabel htmlFor={`${id}-body`}>{skill.name} SKILL.md</FieldLabel>
        <Textarea id={`${id}-body`} className="min-h-48" value={skill.content} readOnly />
        {skill.files.length > 0 && (
          <FieldDescription>Other files: {skill.files.join(", ")}</FieldDescription>
        )}
      </Field>
    </FieldGroup>
  )
}
