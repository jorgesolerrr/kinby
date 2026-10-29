import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  LibraryEntry,
  LibraryGitPreviewResult,
  LibraryKind,
  LibrarySkillCheckResult,
  McpServerResult,
  NewerVersion,
  PreviewedSkill,
  SkillResult,
} from "@kinby/contract"
import { cn } from "cn"
import {
  ArrowLeftIcon,
  CircleXIcon,
  GitBranchIcon,
  LibraryIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { FactoryInstances } from "@/components/factory-instances"
import { McpServerFields } from "@/components/mcp-server-fields"
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
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
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
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { useRead } from "@/hooks/use-read"
import { type Pace, pace, retried } from "@/lib/operation"
import { draftOf, EMPTY_DRAFT, serverOf } from "@/lib/mcp-servers"
import { LIBRARY_SOURCES } from "@/lib/skills"

type Caller = Pick<Client, "call">

const KINDS: { kind: LibraryKind; title: string; noun: string; none: string }[] = [
  { kind: "skill", title: "Skills", noun: "skill", none: "No skills yet" },
  { kind: "mcp_server", title: "MCP servers", noun: "MCP server", none: "No MCP servers yet" },
  { kind: "factory", title: "Factories", noun: "factory", none: "No factories yet" },
]

/**
 * The hub's library: its skills, MCP servers and factories, listed on the left with a search, and
 * the open entry, or a skill being written or added from git or a server being added, filling the
 * rest. Any entry opens to read, and a skill or a server to edit or delete. A narrow screen shows
 * one side at a time.
 */
export function LibraryPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)
  const [kind, setKind] = useState<LibraryKind>("skill")
  const [query, setQuery] = useState("")
  const [opened, setOpened] = useState<LibraryEntry>()
  const [form, setForm] = useState<"write" | "git" | "mcp_server">()
  // A git skill's newer commit is known only from a check, which the list does not run.
  const [checkedNewer, setCheckedNewer] = useState<string[]>([])
  const marked = (name: string, newer: boolean) =>
    setCheckedNewer((names) => [...names.filter((each) => each !== name), ...(newer ? [name] : [])])
  const isNewer = (entry: LibraryEntry) => entry.newer === true || checkedNewer.includes(entry.name)
  const shown =
    opened &&
    (listed?.entries.find((entry) => entry.kind === opened.kind && entry.name === opened.name) ??
      opened)
  const wanted = query.trim().toLocaleLowerCase()
  const entries = listed?.entries.filter(
    (entry) =>
      entry.kind === kind &&
      `${entry.name} ${entry.description ?? ""}`.toLocaleLowerCase().includes(wanted),
  )
  const shownKind = KINDS.find((each) => each.kind === kind)
  const back = () => {
    setForm(undefined)
    setOpened(undefined)
  }

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
    <div className="flex min-h-0 flex-1">
      <section
        aria-label="Entries"
        className={cn(
          "flex min-h-0 w-full flex-col md:w-96 md:shrink-0 md:border-r",
          (form !== undefined || shown !== undefined) && "hidden md:flex",
        )}
      >
        <div className="flex flex-col gap-3 border-b p-4">
          <div className="flex items-center gap-2">
            <h1 className="flex-1 font-heading text-xl font-semibold">Library</h1>
            {kind === "mcp_server" ? (
              <Button size="sm" onClick={() => setForm("mcp_server")}>
                <PlusIcon data-icon="inline-start" />
                Add a server
              </Button>
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={() => setForm("git")}>
                  <GitBranchIcon data-icon="inline-start" />
                  Add from git
                </Button>
                <Button size="sm" onClick={() => setForm("write")}>
                  <PlusIcon data-icon="inline-start" />
                  Write
                </Button>
              </>
            )}
          </div>
          <InputGroup>
            <InputGroupInput
              type="search"
              aria-label="Search the library"
              placeholder="Search the library"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
          </InputGroup>
          <Tabs value={kind} onValueChange={(picked: LibraryKind) => setKind(picked)}>
            <TabsList className="w-full">
              {KINDS.map(({ kind, title }) => (
                <TabsTrigger key={kind} value={kind}>
                  {title}{" "}
                  <span className="font-mono text-xs text-muted-foreground">
                    {listed?.entries.filter((entry) => entry.kind === kind).length}
                  </span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2">
          {failure !== undefined && <Failure error={failure} />}
          {entries === undefined ? (
            failure === undefined && <Skeleton className="h-72 w-full" />
          ) : entries.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>
                  {wanted === "" ? shownKind?.none : `Nothing matches "${query.trim()}"`}
                </EmptyTitle>
              </EmptyHeader>
            </Empty>
          ) : (
            <ItemGroup aria-label={shownKind?.title}>
              {entries.map((entry) => (
                <EntryItem
                  key={entry.name}
                  entry={entry}
                  noun={shownKind?.noun ?? entry.kind}
                  newer={isNewer(entry)}
                  selected={
                    form === undefined && opened?.kind === kind && opened.name === entry.name
                  }
                  onSelect={() => {
                    setForm(undefined)
                    setOpened(entry)
                  }}
                />
              ))}
            </ItemGroup>
          )}
        </div>
      </section>
      {form === "write" ? (
        <Pane title="Write a skill" onBack={back}>
          <div className="flex max-w-2xl flex-col gap-3 p-6">
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
          </div>
        </Pane>
      ) : form === "git" ? (
        <Pane title="Add from git" onBack={back}>
          <AddFromGit
            client={client}
            onAdded={() => {
              setForm(undefined)
              readAgain()
            }}
            onCancel={() => setForm(undefined)}
          />
        </Pane>
      ) : form === "mcp_server" ? (
        <Pane title="Add an MCP server" onBack={back}>
          <NewMcpServer
            client={client}
            onAdded={() => {
              setForm(undefined)
              readAgain()
            }}
            onCancel={() => setForm(undefined)}
          />
        </Pane>
      ) : shown?.kind === "skill" ? (
        <SkillEntry
          key={shown.name}
          client={client}
          clock={clock}
          entry={shown}
          newer={isNewer(shown)}
          onBack={back}
          onSaved={readAgain}
          onChecked={(newer) => marked(shown.name, newer)}
          onMoved={() => {
            marked(shown.name, false)
            readAgain()
          }}
          onDeleted={() => {
            setOpened(undefined)
            readAgain()
          }}
        />
      ) : shown?.kind === "mcp_server" ? (
        <McpServerEntry
          key={shown.name}
          client={client}
          clock={clock}
          entry={shown}
          onBack={back}
          onSaved={readAgain}
          onDeleted={() => {
            setOpened(undefined)
            readAgain()
          }}
        />
      ) : shown?.kind === "factory" ? (
        <FactoryEntry key={shown.name} client={client} clock={clock} entry={shown} onBack={back} />
      ) : (
        <Empty className="hidden md:flex">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <LibraryIcon />
            </EmptyMedia>
            <EmptyTitle>Nothing open</EmptyTitle>
            <EmptyDescription>
              Pick a skill, an MCP server or a factory to read it here.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
    </div>
  )
}

/** One entry's dense row: its name, where it came from, an ember dot for a newer version, and its description. */
function EntryItem({
  entry,
  noun,
  newer,
  selected,
  onSelect,
}: {
  entry: LibraryEntry
  /** What the entry's kind is called. */
  noun: string
  newer: boolean
  selected: boolean
  onSelect: () => void
}) {
  return (
    <Item
      size="xs"
      variant={selected ? "muted" : "default"}
      className="text-left"
      render={
        <button
          type="button"
          aria-label={`${entry.name}, ${entry.source} ${noun}${entry.edited ? ", edited" : ""}${newer ? ", newer" : ""}`}
          aria-current={selected || undefined}
          onClick={onSelect}
        />
      }
    >
      <ItemContent className="min-w-0">
        <ItemTitle className="w-full">
          <span className="min-w-0 flex-1 truncate font-mono">{entry.name}</span>
          {newer && <span aria-hidden className="size-2 shrink-0 rounded-full bg-ember" />}
          <span className="shrink-0 text-xs font-normal text-muted-foreground">
            {LIBRARY_SOURCES[entry.source]}
            {entry.edited && " · edited"}
          </span>
        </ItemTitle>
        {entry.description !== null && <ItemDescription>{entry.description}</ItemDescription>}
      </ItemContent>
    </Item>
  )
}

/** The right side's frame: a header with the back link a narrow screen needs, then `children`. */
function Pane({
  title,
  onBack,
  children,
}: {
  title: string
  onBack: () => void
  children: ReactNode
}) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <header className="flex items-center gap-2 border-b px-6 py-4">
        <BackButton onBack={onBack} />
        <h2 className="font-heading text-base font-semibold">{title}</h2>
      </header>
      {children}
    </section>
  )
}

function BackButton({ onBack }: { onBack: () => void }) {
  return (
    <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label="Back" onClick={onBack}>
      <ArrowLeftIcon />
    </Button>
  )
}

/**
 * An open entry: a header with its name, badges, where it came from and `actions`, then `children`
 * filling the rest.
 */
function EntryPane({
  entry,
  actions,
  onBack,
  children,
}: {
  entry: LibraryEntry
  actions?: ReactNode
  onBack: () => void
  children: ReactNode
}) {
  return (
    <section aria-label={entry.name} className="flex min-w-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-6 py-4">
        <BackButton onBack={onBack} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h2 className="flex flex-wrap items-center gap-2 font-mono text-base font-semibold">
            {entry.name}
            <Badge variant="secondary">{LIBRARY_SOURCES[entry.source]}</Badge>
            {entry.edited && <Badge variant="outline">Edited</Badge>}
          </h2>
          {(entry.git != null || entry.description !== null) && (
            <p className="truncate text-sm text-muted-foreground">
              {entry.git != null ? `${entry.git.url} at ${entry.git.commit}` : entry.description}
            </p>
          )}
        </div>
        {actions}
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-6">{children}</div>
    </section>
  )
}

/** Make one call to the hub at a time, keeping why the hub refused it or how it failed. */
function useHubCall() {
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
  return { busy, refusal, failure, ask }
}

function Refusal({ reason }: { reason: string }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>The hub refused it</AlertTitle>
      <AlertDescription>{reason}</AlertDescription>
    </Alert>
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
  const { busy, refusal, failure, ask } = useHubCall()

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
    <div className="flex max-w-2xl flex-col gap-4 p-6">
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
    </div>
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

/**
 * A library skill's `SKILL.md` to edit at full height, a shipped or git skill's newer version to
 * move to, and Delete, with the actions in its header. An instance that picked it keeps its copy.
 */
function SkillEntry({
  client,
  clock,
  entry,
  newer,
  onBack,
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
  onBack: () => void
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
      setNotice("saved")
      onSaved()
    })
  const remove = (read: SkillResult) =>
    act(async () => {
      await client.call("library.skill.delete", { name, hash: read.hash })
      onDeleted()
    })

  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      {version.checked !== undefined && version.checked.newer === null && (
        <span className="text-sm text-muted-foreground">No newer version.</span>
      )}
      {entry.source !== "written" && (
        <Button
          variant="outline"
          size="sm"
          disabled={version.busy}
          onClick={() => void version.check()}
        >
          {version.busy && <Spinner data-icon="inline-start" />}
          Check for a newer version
        </Button>
      )}
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
  )

  return (
    <EntryPane entry={entry} actions={actions} onBack={onBack}>
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
      {entry.source !== "written" && (
        <NewerVersionNotice
          name={name}
          newer={newer}
          version={version}
          edited={entry.edited === true}
          unsaved={opened !== undefined && draft !== opened.content}
        />
      )}
      {opened === undefined ? (
        failure === undefined && <Skeleton className="min-h-72 w-full flex-1" />
      ) : (
        <Field className="min-h-96 flex-1" data-invalid={invalid !== undefined || undefined}>
          <FieldLabel htmlFor={id}>SKILL.md</FieldLabel>
          <Textarea
            id={id}
            className="min-h-0 flex-1 resize-none"
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
    </EntryPane>
  )
}

/**
 * Check a shipped or git skill for a newer version, and move the library's skill to the one a
 * check showed. A skill the user edited moves over the edits, once they agree to it.
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
  return { checked, busy, refusal, failure, check, move }
}

/**
 * That a newer version is out, with its diff once checked and the move to it. A skill with unsaved
 * edits moves only once they are saved.
 */
function NewerVersionNotice({
  name,
  newer: known,
  version,
  edited,
  unsaved,
}: {
  name: string
  newer: boolean
  version: ReturnType<typeof useNewerVersion>
  edited: boolean
  unsaved: boolean
}) {
  const id = useId()
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
              {edited ? (
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
      ) : (
        known && (
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
        )
      )}
    </section>
  )
}

/** A factory's `factory.yaml` at full height, read-only, its other files, and the instances holding its roles. */
function FactoryEntry({
  client,
  clock,
  entry,
  onBack,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
  onBack: () => void
}) {
  const { name } = entry
  const id = useId()
  const read = useCallback(async () => {
    const { "factory.yaml": content = "", ...others } = (await client.call("factory.get", { name }))
      .files
    return { content, files: Object.keys(others) }
  }, [client, name])
  const { value: opened, failure } = useRead(read, clock)

  return (
    <EntryPane entry={entry} onBack={onBack}>
      {opened === undefined ? (
        failure === undefined ? (
          <Skeleton className="min-h-72 w-full flex-1" />
        ) : (
          <Failure error={failure} />
        )
      ) : (
        <>
          <Field className="min-h-96 flex-1">
            <FieldLabel htmlFor={id}>factory.yaml</FieldLabel>
            <Textarea
              id={id}
              className="min-h-0 flex-1 resize-none"
              value={opened.content}
              readOnly
            />
            {opened.files.length > 0 && (
              <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
            )}
          </Field>
          <FactoryInstances client={client} clock={clock} factory={name} />
        </>
      )}
    </EntryPane>
  )
}

/** A new MCP server: its name and its definition. */
function NewMcpServer({
  client,
  onAdded,
  onCancel,
}: {
  client: Caller
  onAdded: () => void
  onCancel: () => void
}) {
  const id = useId()
  const [name, setName] = useState("")
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const { busy, refusal, failure, ask } = useHubCall()

  const add = () =>
    ask(async () => {
      await client.call("library.mcp_server.write", { name, server: serverOf(draft), hash: null })
      onAdded()
    })

  return (
    <div className="flex max-w-2xl flex-col gap-4 p-6">
      {failure !== undefined && <Failure error={failure} />}
      {refusal !== undefined && <Refusal reason={refusal} />}
      <Field>
        <FieldLabel htmlFor={id}>Name</FieldLabel>
        <Input id={id} value={name} onChange={(event) => setName(event.target.value)} />
        <FieldDescription>
          Lowercase letters, digits and hyphens. Its tools are named after it.
        </FieldDescription>
      </Field>
      <McpServerFields draft={draft} disabled={busy} onChange={setDraft} />
      <Field orientation="horizontal">
        <Button disabled={busy || name === ""} onClick={() => void add()}>
          {busy && <Spinner data-icon="inline-start" />}
          Add
        </Button>
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </Field>
    </div>
  )
}

/** A library MCP server, read from the hub, to edit or delete. */
function McpServerEntry({
  client,
  clock,
  entry,
  onBack,
  onSaved,
  onDeleted,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
  onBack: () => void
  onSaved: () => void
  onDeleted: () => void
}) {
  const { name } = entry
  const read = useCallback(() => client.call("library.mcp_server.read", { name }), [client, name])
  const { value: opened, failure } = useRead(read, clock)

  if (opened === undefined) {
    return (
      <EntryPane entry={entry} onBack={onBack}>
        {failure === undefined ? (
          <Skeleton className="min-h-72 w-full flex-1" />
        ) : (
          <Failure error={failure} />
        )}
      </EntryPane>
    )
  }
  return (
    <McpServerEditor
      client={client}
      entry={entry}
      opened={opened}
      onBack={onBack}
      onSaved={onSaved}
      onDeleted={onDeleted}
    />
  )
}

/** An MCP server's definition to edit, saved and deleted over the version `opened` read. */
function McpServerEditor({
  client,
  entry,
  opened,
  onBack,
  onSaved,
  onDeleted,
}: {
  client: Caller
  entry: LibraryEntry
  opened: McpServerResult
  onBack: () => void
  onSaved: () => void
  onDeleted: () => void
}) {
  const [current, setCurrent] = useState(opened)
  const [draft, setDraft] = useState(() => draftOf(opened.server))
  const [saved, setSaved] = useState(false)
  const [stale, setStale] = useState(false)
  const { busy, refusal, failure, ask } = useHubCall()
  const { name, hash } = current

  const unlessStale = (call: () => Promise<void>) =>
    ask(async () => {
      try {
        await call()
      } catch (error) {
        if (!(error instanceof CallError && error.code === "STALE")) throw error
        setStale(true)
      }
    })
  const save = () =>
    unlessStale(async () => {
      setCurrent(
        await client.call("library.mcp_server.write", { name, server: serverOf(draft), hash }),
      )
      setSaved(true)
      onSaved()
    })
  const remove = () =>
    unlessStale(async () => {
      await client.call("library.mcp_server.delete", { name, hash })
      onDeleted()
    })
  const loadAgain = () =>
    ask(async () => {
      const read = await client.call("library.mcp_server.read", { name })
      setCurrent(read)
      setDraft(draftOf(read.server))
      setStale(false)
    })

  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      <AlertDialog>
        <AlertDialogTrigger render={<Button variant="destructive" size="sm" disabled={busy} />}>
          <Trash2Icon data-icon="inline-start" />
          Delete
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
            <AlertDialogDescription>Its definition leaves the library.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void remove()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <Button size="sm" disabled={busy} onClick={() => void save()}>
        {busy && <Spinner data-icon="inline-start" />}
        Save
      </Button>
    </div>
  )

  return (
    <EntryPane entry={entry} actions={actions} onBack={onBack}>
      {saved && <p className="text-sm text-muted-foreground">Saved.</p>}
      {stale && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>Changed since you opened it</AlertTitle>
          <AlertDescription>
            {name} was saved since you opened it. Load it again to see it. Your edits here are
            dropped.
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void loadAgain()}>
              Load again
            </Button>
          </AlertAction>
        </Alert>
      )}
      {failure !== undefined && <Failure error={failure} />}
      {refusal !== undefined && <Refusal reason={refusal} />}
      <div className="max-w-2xl">
        <McpServerFields
          draft={draft}
          disabled={busy}
          onChange={(changed) => {
            setDraft(changed)
            setSaved(false)
          }}
        />
      </div>
    </EntryPane>
  )
}
