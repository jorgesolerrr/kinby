import { CallError } from "@kinby/contract"
import type { Client, Clock, LibraryEntry, McpServerResult } from "@kinby/contract"
import { CheckIcon, CircleXIcon, CopyIcon, Trash2Icon } from "lucide-react"
import { useCallback, useId, useState } from "react"

import { Failure, Refusal } from "@/components/config-alerts"
import { McpServerFields } from "@/components/mcp-server-fields"
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
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useHubCall } from "@/hooks/use-hub-call"
import { useRead } from "@/hooks/use-read"
import {
  copiedEntry,
  draftOf,
  EMPTY_DRAFT,
  pastedServers,
  reasonsByField,
  serverOf,
} from "@/lib/mcp-servers"
import type { PastedServer } from "@/lib/mcp-servers"

type Caller = Pick<Client, "call">

/**
 * A new MCP server: its name and its definition, typed or filled from a pasted entry. A pasted
 * block's servers come one after the other, each added or skipped.
 */
export function NewMcpServer({
  client,
  onAdded,
  onClose,
}: {
  client: Caller
  onAdded: () => void
  onClose: () => void
}) {
  const id = useId()
  const [pasted, setPasted] = useState("")
  const [name, setName] = useState("")
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [next, setNext] = useState<PastedServer[]>([])
  const { busy, refused, failure, ask, reset } = useHubCall()
  const { name: nameRefused, other, ...fieldsRefused } = reasonsByField(refused)
  const unread = pasted.trim() !== ""

  const fill = ([server, ...rest]: PastedServer[]) => {
    if (server === undefined) return onClose()
    if (server.name !== undefined) setName(server.name)
    setDraft(draftOf({ entry: server.entry }))
    setNext(rest)
    reset()
  }
  const paste = (text: string) => {
    const servers = pastedServers(text)
    setPasted(servers === undefined ? text : "")
    if (servers !== undefined) fill(servers)
  }
  const add = () =>
    ask(async () => {
      await client.call("library.mcp_server.write", { name, server: serverOf(draft), hash: null })
      onAdded()
      fill(next)
    })

  return (
    <div className="flex flex-col gap-4">
      {failure !== undefined && <Failure error={failure} />}
      {other !== undefined && <Refusal reason={other} />}
      <Field data-invalid={unread || undefined}>
        <FieldLabel htmlFor={`${id}-pasted`}>Paste an entry</FieldLabel>
        <Textarea
          id={`${id}-pasted`}
          value={pasted}
          aria-invalid={unread || undefined}
          onChange={(event) => paste(event.target.value)}
        />
        <FieldDescription>
          An mcpServers entry or block, as Claude Code or Cursor writes it. It fills the fields
          below, to review before you add it.
        </FieldDescription>
        {unread && (
          <FieldError>
            This is not an http or stdio entry, nor an mcpServers block of them.
          </FieldError>
        )}
      </Field>
      <Field data-invalid={nameRefused !== undefined || undefined}>
        <FieldLabel htmlFor={id}>Name</FieldLabel>
        <Input
          id={id}
          value={name}
          aria-invalid={nameRefused !== undefined || undefined}
          onChange={(event) => setName(event.target.value)}
        />
        <FieldDescription>
          Lowercase letters, digits and hyphens. Its tools are named after it.
        </FieldDescription>
        {nameRefused !== undefined && <FieldError>{nameRefused}</FieldError>}
      </Field>
      <McpServerFields draft={draft} refused={fieldsRefused} disabled={busy} onChange={setDraft} />
      {next.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Next from the paste: {next.map((server) => server.name).join(", ")}.
        </p>
      )}
      <Field orientation="horizontal">
        <Button disabled={busy || name === ""} onClick={() => void add()}>
          {busy && <Spinner data-icon="inline-start" />}
          Add
        </Button>
        {next.length > 0 && (
          <Button variant="outline" disabled={busy} onClick={() => fill(next)}>
            Skip
          </Button>
        )}
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
      </Field>
    </div>
  )
}

/** A library MCP server unfolded: its definition, read from the hub, to edit or delete. */
export function McpServerEntry({
  client,
  clock,
  entry,
  onSaved,
  onDeleted,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
  onSaved: () => void
  onDeleted: () => void
}) {
  const { name } = entry
  const read = useCallback(() => client.call("library.mcp_server.read", { name }), [client, name])
  const { value: opened, failure } = useRead(read, clock)

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  return <McpServerEditor client={client} opened={opened} onSaved={onSaved} onDeleted={onDeleted} />
}

/** An MCP server's definition to edit, saved and deleted over the version `opened` read. */
function McpServerEditor({
  client,
  opened,
  onSaved,
  onDeleted,
}: {
  client: Caller
  opened: McpServerResult
  onSaved: () => void
  onDeleted: () => void
}) {
  const [current, setCurrent] = useState(opened)
  const [draft, setDraft] = useState(() => draftOf(opened.server))
  const [saved, setSaved] = useState(false)
  const [stale, setStale] = useState(false)
  const [copied, setCopied] = useState(false)
  const { busy, refused, failure, ask } = useHubCall()
  const { name: named, other, ...fieldsRefused } = reasonsByField(refused)
  const unshown = [named, other].filter((reason) => reason !== undefined).join(" ")
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
  const copy = async () => {
    await navigator.clipboard.writeText(copiedEntry(name, serverOf(draft)))
    setCopied(true)
    setTimeout(() => setCopied(false), 2_000)
  }

  return (
    <>
      <McpServerFields
        draft={draft}
        refused={fieldsRefused}
        disabled={busy}
        onChange={(changed) => {
          setDraft(changed)
          setSaved(false)
        }}
      />
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
      {unshown !== "" && <Refusal reason={unshown} />}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => void copy()}>
          {copied ? <CheckIcon data-icon="inline-start" /> : <CopyIcon data-icon="inline-start" />}
          Copy as JSON
        </Button>
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
    </>
  )
}
