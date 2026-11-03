import { CallError } from "@kinby/contract"
import type { Client, Clock, LibraryEntry, McpServerResult } from "@kinby/contract"
import { CircleXIcon, Trash2Icon } from "lucide-react"
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
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useHubCall } from "@/hooks/use-hub-call"
import { useRead } from "@/hooks/use-read"
import { draftOf, EMPTY_DRAFT, serverOf } from "@/lib/mcp-servers"

type Caller = Pick<Client, "call">

/** A new MCP server: its name and its definition. */
export function NewMcpServer({
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
    <div className="flex flex-col gap-4">
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

  return (
    <>
      <McpServerFields
        draft={draft}
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
      {refusal !== undefined && <Refusal reason={refusal} />}
      <div className="flex flex-wrap items-center justify-end gap-2">
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
