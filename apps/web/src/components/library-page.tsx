import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  LibraryEntry,
  LibraryGitPreviewResult,
  LibraryKind,
  PreviewedSkill,
  SkillResult,
} from "@kinby/contract"
import { CircleXIcon, Trash2Icon } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
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
import { Badge } from "@/components/ui/badge"
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
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { useRead } from "@/hooks/use-read"
import { type Pace, pace, retried } from "@/lib/operation"
import { LIBRARY_SOURCES } from "@/lib/skills"

type Caller = Pick<Client, "call">

const KINDS: { kind: LibraryKind; title: string }[] = [
  { kind: "skill", title: "Skills" },
  { kind: "factory", title: "Factories" },
]

/**
 * The hub's library: the skills and factories an instance can pick. Any entry opens to read, and a
 * skill to edit or delete.
 */
export function LibraryPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  const [opened, setOpened] = useState<LibraryEntry>()
  const [form, setForm] = useState<"write" | "git">()

  const writeNew: WriteNew = async (name, content) => {
    try {
      await client.call("library.skill.write", { name, content, hash: null })
    } catch (error) {
      if (error instanceof CallError && error.code === "INVALID_ARGUMENT") return error.fields
      throw error
    }
    return undefined
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex max-w-2xl items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Library</h1>
        {form === undefined && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setForm("write")}>
              Write a skill
            </Button>
            <Button variant="outline" size="sm" onClick={() => setForm("git")}>
              Add from git
            </Button>
          </div>
        )}
      </div>
      {form === "write" && (
        <section className="flex max-w-2xl flex-col gap-3" aria-label="Write a skill">
          <NewSkill
            write={writeNew}
            onCreated={() => {
              setForm(undefined)
              readAgain()
            }}
          />
          <div>
            <Button variant="outline" onClick={() => setForm(undefined)}>
              Cancel
            </Button>
          </div>
        </section>
      )}
      {form === "git" && (
        <AddFromGit
          client={client}
          onAdded={() => {
            setForm(undefined)
            readAgain()
          }}
          onCancel={() => setForm(undefined)}
        />
      )}
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined
        ? failure === undefined && <Skeleton className="h-72 w-full max-w-2xl" />
        : KINDS.map(({ kind, title }) => (
            <section key={kind} className="flex max-w-2xl flex-col gap-2">
              <h2 className="font-medium">{title}</h2>
              <ItemGroup aria-label={title}>
                {listed.entries
                  .filter((entry) => entry.kind === kind)
                  .map((entry) => (
                    <EntryItem
                      key={entry.name}
                      entry={entry}
                      selected={opened?.kind === kind && opened.name === entry.name}
                      onSelect={() => setOpened(entry)}
                    />
                  ))}
              </ItemGroup>
            </section>
          ))}
      {opened !== undefined && (
        <>
          <Separator className="max-w-2xl" />
          {opened.kind === "skill" ? (
            <SkillEntry
              key={opened.name}
              client={client}
              clock={clock}
              name={opened.name}
              onSaved={readAgain}
              onDeleted={() => {
                setOpened(undefined)
                readAgain()
              }}
            />
          ) : (
            <FactoryEntry key={opened.name} client={client} clock={clock} name={opened.name} />
          )}
        </>
      )}
    </div>
  )
}

function EntryItem({
  entry,
  selected,
  onSelect,
}: {
  entry: LibraryEntry
  selected: boolean
  onSelect: () => void
}) {
  return (
    <Item
      size="sm"
      variant={selected ? "outline" : "default"}
      className="text-left"
      render={
        <button
          type="button"
          aria-label={`${entry.name}, ${entry.source} ${entry.kind}${entry.edited ? ", edited" : ""}`}
          aria-current={selected || undefined}
          onClick={onSelect}
        />
      }
    >
      <ItemContent>
        <ItemTitle>
          {entry.name}
          <Badge variant="secondary">{LIBRARY_SOURCES[entry.source]}</Badge>
          {entry.edited && <Badge variant="outline">Edited</Badge>}
        </ItemTitle>
        {entry.description !== null && <ItemDescription>{entry.description}</ItemDescription>}
        {entry.git != null && (
          <ItemDescription>
            {entry.git.url} at {entry.git.commit}
          </ItemDescription>
        )}
      </ItemContent>
    </Item>
  )
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
function AddFromGit({
  client,
  onAdded,
  onCancel,
}: {
  client: Caller
  onAdded: () => void
  onCancel: () => void
}) {
  const id = useId()
  const [address, setAddress] = useState("")
  const [preview, setPreview] = useState<Previewed>()
  const [chosen, setChosen] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string>()
  const [failure, setFailure] = useState<unknown>()

  const ask = async (call: () => Promise<void>) => {
    setBusy(true)
    setRefusal(undefined)
    setFailure(undefined)
    try {
      await call()
    } catch (error) {
      if (error instanceof CallError && Object.keys(error.fields).length > 0) {
        setRefusal(Object.values(error.fields).join(" "))
      } else setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  const previewAddress = (event: FormEvent) => {
    event.preventDefault()
    void ask(async () => {
      const shown = await client.call("library.git.preview", { address })
      setPreview({ address, shown })
      setChosen(shown.skills.map((skill) => skill.name))
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
    <section className="flex max-w-2xl flex-col gap-4" aria-label="Add from git">
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
      {refusal !== undefined && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The hub refused it</AlertTitle>
          <AlertDescription>{refusal}</AlertDescription>
        </Alert>
      )}
      {preview !== undefined && (
        <FieldSet>
          <FieldLegend>Skills to add</FieldLegend>
          <FieldDescription>At commit {preview.shown.commit}</FieldDescription>
          <FieldGroup>
            {preview.shown.skills.map((skill) => (
              <SkillPreview
                key={skill.name}
                skill={skill}
                chosen={chosen.includes(skill.name)}
                onChosen={(on) =>
                  setChosen((names) =>
                    on ? [...names, skill.name] : names.filter((name) => name !== skill.name),
                  )
                }
              />
            ))}
          </FieldGroup>
          <Field orientation="horizontal">
            <Button disabled={busy || chosen.length === 0} onClick={() => add(preview, chosen)}>
              Add {chosen.length} {chosen.length === 1 ? "skill" : "skills"}
            </Button>
          </Field>
        </FieldSet>
      )}
    </section>
  )
}

/** One skill a preview found: whether to add it, and what the model would read. */
function SkillPreview({
  skill,
  chosen,
  onChosen,
}: {
  skill: PreviewedSkill
  chosen: boolean
  onChosen: (chosen: boolean) => void
}) {
  const id = useId()
  return (
    <FieldGroup>
      <Field orientation="horizontal">
        <Checkbox id={`${id}-choose`} checked={chosen} onCheckedChange={onChosen} />
        <FieldContent>
          <FieldLabel htmlFor={`${id}-choose`}>{skill.name}</FieldLabel>
          <FieldDescription>{skill.path}</FieldDescription>
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

/** A library skill's `SKILL.md` to edit, and Delete. An instance that picked it keeps its copy. */
function SkillEntry({
  client,
  clock,
  name,
  onSaved,
  onDeleted,
}: {
  client: Caller
  clock: Clock
  name: string
  onSaved: () => void
  onDeleted: () => void
}) {
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

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full max-w-2xl" />
    ) : (
      <Failure error={failure} />
    )
  }
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
  const save = () =>
    act(async () => {
      show(await client.call("library.skill.write", { name, content: draft, hash: opened.hash }))
      setNotice("saved")
      onSaved()
    })
  const remove = () =>
    act(async () => {
      await client.call("library.skill.delete", { name, hash: opened.hash })
      onDeleted()
    })

  return (
    <div className="flex max-w-2xl flex-col gap-3">
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
      <div className="flex items-center gap-3">
        <Button disabled={busy || draft === opened.content} onClick={() => void save()}>
          {busy && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">
            Saved. Instances take it when they pick it or update.
          </span>
        )}
        <AlertDialog>
          <AlertDialogTrigger
            render={<Button className="ml-auto" variant="destructive" disabled={busy} />}
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
              <AlertDialogAction variant="destructive" onClick={() => void remove()}>
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  )
}

/** A factory's `factory.yaml`, read-only, and its other files. */
function FactoryEntry({ client, clock, name }: { client: Caller; clock: Clock; name: string }) {
  const id = useId()
  const read = useCallback(async () => {
    const { "factory.yaml": content = "", ...others } = (await client.call("factory.get", { name }))
      .files
    return { content, files: Object.keys(others) }
  }, [client, name])
  const { value: opened, failure } = useRead(read, clock)

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full max-w-2xl" />
    ) : (
      <Failure error={failure} />
    )
  }
  return (
    <Field className="max-w-2xl">
      <FieldLabel htmlFor={id}>factory.yaml</FieldLabel>
      <Textarea id={id} className="min-h-72" value={opened.content} readOnly />
      {opened.files.length > 0 && (
        <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
      )}
    </Field>
  )
}
