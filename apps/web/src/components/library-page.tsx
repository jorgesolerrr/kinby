import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  LibraryEntry,
  LibraryGitPreviewResult,
  LibraryKind,
  PreviewedSkill,
} from "@kinby/contract"
import { CircleXIcon } from "lucide-react"
import { type FormEvent, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"
import { LIBRARY_SOURCES } from "@/lib/skills"

type Caller = Pick<Client, "call">

const KINDS: { kind: LibraryKind; title: string }[] = [
  { kind: "skill", title: "Skills" },
  { kind: "factory", title: "Factories" },
]

/** The hub's library: the skills and factories an instance can pick. Any entry opens to read. */
export function LibraryPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  const [opened, setOpened] = useState<LibraryEntry>()
  const [adding, setAdding] = useState(false)

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex max-w-2xl items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Library</h1>
        {!adding && (
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            Add from git
          </Button>
        )}
      </div>
      {adding && (
        <AddFromGit
          client={client}
          onAdded={() => {
            setAdding(false)
            readAgain()
          }}
          onCancel={() => setAdding(false)}
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
          <EntryView
            key={`${opened.kind} ${opened.name}`}
            client={client}
            clock={clock}
            entry={opened}
          />
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
          aria-label={`${entry.name}, ${entry.source} ${entry.kind}`}
          aria-current={selected || undefined}
          onClick={onSelect}
        />
      }
    >
      <ItemContent>
        <ItemTitle>
          {entry.name}
          <Badge variant="secondary">{LIBRARY_SOURCES[entry.source]}</Badge>
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

/** The entry's main file, a skill's `SKILL.md` or a factory's `factory.yaml`, and its others. */
interface Opened {
  file: string
  content: string
  files: string[]
}

async function openEntry(client: Caller, entry: LibraryEntry): Promise<Opened> {
  if (entry.kind === "skill") {
    const skill = await client.call("library.skill.read", { name: entry.name })
    return { file: "SKILL.md", content: skill.content, files: skill.files }
  }
  const { "factory.yaml": content = "", ...others } = (
    await client.call("factory.get", { name: entry.name })
  ).files
  return { file: "factory.yaml", content, files: Object.keys(others) }
}

function EntryView({
  client,
  clock,
  entry,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
}) {
  const id = useId()
  const read = useCallback(() => openEntry(client, entry), [client, entry])
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
      <FieldLabel htmlFor={id}>{opened.file}</FieldLabel>
      <Textarea id={id} className="min-h-72" value={opened.content} readOnly />
      {opened.files.length > 0 && (
        <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
      )}
    </Field>
  )
}
