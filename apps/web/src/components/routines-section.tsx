import { CallError } from "@kinby/contract"
import type { Clock, InstanceClient, RoutineFile, RoutineSummary } from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
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
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { useRead } from "@/hooks/use-read"
import { lastChanged, when } from "@/lib/config-changes"
import { type Pace, pace, retried } from "@/lib/operation"
import {
  deleteRoutine,
  failuresInARow,
  lastFiring,
  type ListedRoutine,
  listRoutines,
  NEW_ROUTINE,
  nextFiring,
  saveRoutine,
  trigger,
} from "@/lib/routines"
import {
  ArrowLeftIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  ShieldCheckIcon,
  TextCursorInputIcon,
  Trash2Icon,
} from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** The instance's routines, each with its switch, or the editor of the one `opened` names. */
export function RoutinesSection({
  client,
  clock,
  opened,
}: {
  client: Caller
  clock: Clock
  opened?: string
}) {
  const pacing = usePace(clock)
  const [routines, setRoutines] = useState<ListedRoutine[]>()
  const [failure, setFailure] = useState<unknown>()
  const [started, setStarted] = useState<string>()
  // The routine open in the editor, null for a new one, or undefined for the list.
  const [editing, setEditing] = useState<string | null | undefined>(opened)

  const load = useCallback(
    (pacing: Pace) =>
      retried(() => listRoutines(client), pacing).then(
        (listed) => {
          setRoutines(listed)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client],
  )
  useEffect(() => {
    const pacing = pace(clock)
    void load(pacing)
    return pacing.stop
  }, [clock, load])

  const act = (action: () => Promise<unknown>) =>
    action().then(
      () => load(pacing),
      (error: unknown) => setFailure(error),
    )
  const toggle = (name: string, enabled: boolean) =>
    act(() => client.call("routine.set_enabled", { name, enabled }))
  const run = (name: string) =>
    act(async () => {
      await client.call("routine.run", { name })
      setStarted(name)
    })
  // The list reads again either way: the new name, or the hash a retry needs after a refusal.
  // The rename settles only once it has, so the dialog offers no retry over the old hash.
  const rename = (name: string, newName: string, hash: string) =>
    client.call("routine.rename", { name, new_name: newName, hash }).finally(() => load(pacing))

  if (editing !== undefined) {
    return (
      <RoutineEditor
        client={client}
        clock={clock}
        name={editing}
        onClose={() => {
          setEditing(undefined)
          void load(pacing)
        }}
      />
    )
  }
  if (routines === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-40 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button size="sm" onClick={() => setEditing(null)}>
          <PlusIcon data-icon="inline-start" />
          New routine
        </Button>
      </div>
      {failure !== undefined && <Failure error={failure} />}
      {started !== undefined && (
        <p className="text-sm text-muted-foreground">
          Started {started}. Its turn is in a new thread.
        </p>
      )}
      <ItemGroup aria-label="Routines">
        {routines.map((listed) => (
          <RoutineItem
            key={listed.summary.name}
            client={client}
            clock={clock}
            listed={listed}
            onToggle={(enabled) => void toggle(listed.summary.name, enabled)}
            onRun={() => void run(listed.summary.name)}
            onEdit={() => setEditing(listed.summary.name)}
            onRename={(newName) => rename(listed.summary.name, newName, listed.hash)}
          />
        ))}
      </ItemGroup>
    </div>
  )
}

function RoutineItem({
  client,
  clock,
  listed: { summary, hash, lastChange },
  onToggle,
  onRun,
  onEdit,
  onRename,
}: {
  client: Caller
  clock: Clock
  listed: ListedRoutine
  onToggle: (enabled: boolean) => void
  onRun: () => void
  onEdit: () => void
  onRename: (newName: string) => Promise<unknown>
}) {
  const failures = summary.failure_count ?? 0
  return (
    <Item render={<li />} aria-label={summary.name} variant="outline">
      <ItemContent>
        <ItemTitle>{summary.name}</ItemTitle>
        <ItemDescription>{summary.description}</ItemDescription>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span>{trigger(summary)}</span>
          <span>{nextFiring(summary)}</span>
          <span>{lastFiring(summary)}</span>
        </div>
        {(failures > 0 || summary.pending > 0) && (
          <div className="flex flex-wrap gap-2">
            {failures > 0 && <Badge variant="destructive">{failuresInARow(failures)}</Badge>}
            {summary.pending > 0 && (
              <Badge variant="secondary">
                {summary.pending} pending {summary.pending === 1 ? "delivery" : "deliveries"}
              </Badge>
            )}
          </div>
        )}
        <ItemDescription>{lastChanged(lastChange, hash)}</ItemDescription>
        {/* Any change to the routine revokes its grants, so they are read again with each hash. */}
        <RoutineGrants key={hash} client={client} clock={clock} name={summary.name} />
      </ItemContent>
      <ItemActions>
        <Switch aria-label="On" checked={summary.enabled} onCheckedChange={onToggle} />
        <Button size="sm" variant="outline" onClick={onRun}>
          <PlayIcon data-icon="inline-start" />
          Run now
        </Button>
        <Button size="sm" variant="outline" onClick={onEdit}>
          <PencilIcon data-icon="inline-start" />
          Edit
        </Button>
        <RenameDialog summary={summary} onRename={onRename} />
      </ItemActions>
    </Item>
  )
}

/** The tools the routine always allows, each with its revoke. Nothing when it allows none. */
function RoutineGrants({ client, clock, name }: { client: Caller; clock: Clock; name: string }) {
  const list = useCallback(() => client.call("routine.grant.list", { name }), [client, name])
  const { value, failure, readAgain } = useRead(list, clock)
  const [refusal, setRefusal] = useState<unknown>()

  const revoke = (tool: string) =>
    client.call("routine.grant.revoke", { name, tool }).then(
      () => {
        setRefusal(undefined)
        readAgain()
      },
      (error: unknown) => setRefusal(error),
    )

  const shown = refusal ?? failure
  if (shown !== undefined) return <Failure error={shown} />
  if (value === undefined || value.grants.length === 0) return null
  return (
    <ul aria-label="Always allowed" className="flex flex-col gap-1 text-sm text-muted-foreground">
      {value.grants.map((grant) => (
        <li key={grant.tool} className="flex items-center gap-2">
          <ShieldCheckIcon aria-hidden className="size-3.5" />
          <span>
            Always allows <span className="font-mono text-foreground">{grant.tool}</span>, since{" "}
            {when(grant.granted_at)}
          </span>
          <Button
            size="xs"
            variant="ghost"
            aria-label={`Revoke ${grant.tool}`}
            onClick={() => void revoke(grant.tool)}
          >
            Revoke
          </Button>
        </li>
      ))}
    </ul>
  )
}

/**
 * Asks for a routine's new name. Its whole directory moves, and its signal path with it, so the
 * old path stops answering. A refusal shows in the dialog, and a refused name under its field.
 */
function RenameDialog({
  summary,
  onRename,
}: {
  summary: RoutineSummary
  onRename: (newName: string) => Promise<unknown>
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState("")
  const [renaming, setRenaming] = useState(false)
  const [invalid, setInvalid] = useState<string>()
  const [failure, setFailure] = useState<unknown>()

  const rename = async () => {
    setRenaming(true)
    setInvalid(undefined)
    setFailure(undefined)
    try {
      await onRename(newName)
      setOpen(false)
    } catch (error) {
      if (error instanceof CallError && error.fields.new_name !== undefined) {
        setInvalid(error.fields.new_name)
      } else setFailure(error)
    } finally {
      setRenaming(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" variant="outline" />}>
        <TextCursorInputIcon data-icon="inline-start" />
        Rename
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename {summary.name}</DialogTitle>
          <DialogDescription>
            This moves routines/{summary.name} and every file in it.
          </DialogDescription>
        </DialogHeader>
        {failure !== undefined && <Failure error={failure} />}
        <Field data-invalid={invalid !== undefined || undefined}>
          <FieldLabel htmlFor={id}>New name</FieldLabel>
          <Input
            id={id}
            value={newName}
            readOnly={renaming}
            aria-invalid={invalid !== undefined || undefined}
            onChange={(event) => {
              setNewName(event.target.value)
              setInvalid(undefined)
            }}
          />
          <FieldDescription>Letters, digits, hyphens, and underscores.</FieldDescription>
          <FieldError>{invalid}</FieldError>
        </Field>
        {summary.signal && (
          <p className="text-sm text-muted-foreground">
            Its webhook path changes from {summary.signal.path} to /signals/{newName}. The old path
            stops answering.
          </p>
        )}
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button disabled={renaming || newName === ""} onClick={() => void rename()}>
            {renaming && <Spinner data-icon="inline-start" />}
            Rename
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * One routine's ROUTINE.md as text, or a new routine when `name` is null. A save carries the hash
 * of the routine's directory as it was read, so a save over a change made since is refused, and
 * "Load theirs" reads it again. A new routine saves with a null hash, refused if it exists.
 */
function RoutineEditor({
  client,
  clock,
  name,
  onClose,
}: {
  client: Caller
  clock: Clock
  name: string | null
  onClose: () => void
}) {
  const nameId = useId()
  const contentId = useId()
  const pacing = usePace(clock)
  const [opened, setOpened] = useState<RoutineFile>()
  const [newName, setNewName] = useState("")
  const [draft, setDraft] = useState(name === null ? NEW_ROUTINE : "")
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [failure, setFailure] = useState<unknown>()
  const [confirming, setConfirming] = useState(false)
  const routine = opened?.name ?? name ?? newName

  const show = (read: RoutineFile) => {
    setOpened(read)
    setDraft(read.content)
  }
  const load = useCallback(
    (target: string, pacing: Pace) =>
      retried(() => client.call("routine.read", { name: target }), pacing).then(
        (read) => {
          setOpened(read)
          setDraft(read.content)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client],
  )
  useEffect(() => {
    if (name === null) return
    const pacing = pace(clock)
    void load(name, pacing)
    return pacing.stop
  }, [clock, load, name])

  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await saveRoutine(client, routine, draft, opened?.hash ?? null)
      if (saved === "stale") {
        setNotice("stale")
      } else {
        show(saved)
        setNotice("saved")
      }
    } catch (error) {
      setFailure(error)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (read: RoutineFile) => {
    setConfirming(false)
    setFailure(undefined)
    try {
      if ((await deleteRoutine(client, read.name, read.hash)) === "stale") setNotice("stale")
      else onClose()
    } catch (error) {
      setFailure(error)
    }
  }

  const creating = name === null && opened === undefined
  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          <ArrowLeftIcon data-icon="inline-start" />
          Back to routines
        </Button>
      </div>
      {notice === "stale" && (
        <StaleAlert file={`routines/${routine}`} onLoad={() => void load(routine, pacing)} />
      )}
      {failure !== undefined && <Failure error={failure} />}
      {opened === undefined && !creating ? (
        failure === undefined && <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <FieldGroup>
            {creating && (
              <Field>
                <FieldLabel htmlFor={nameId}>Name</FieldLabel>
                <Input
                  id={nameId}
                  value={newName}
                  readOnly={saving}
                  onChange={(event) => setNewName(event.target.value)}
                />
                <FieldDescription>
                  The routine's directory. Letters, digits, hyphens, and underscores.
                </FieldDescription>
              </Field>
            )}
            <Field>
              <FieldLabel htmlFor={contentId}>ROUTINE.md</FieldLabel>
              <Textarea
                id={contentId}
                className="min-h-72"
                value={draft}
                readOnly={saving}
                onChange={(event) => {
                  setDraft(event.target.value)
                  if (notice === "saved") setNotice(undefined)
                }}
              />
            </Field>
          </FieldGroup>
          <div className="flex items-center gap-3">
            <Button
              disabled={saving || routine === "" || draft === opened?.content}
              onClick={() => void save()}
            >
              {saving && <Spinner data-icon="inline-start" />}
              Save
            </Button>
            {notice === "saved" && <span className="text-sm text-muted-foreground">Saved.</span>}
            {opened !== undefined && (
              <AlertDialog open={confirming} onOpenChange={setConfirming}>
                <AlertDialogTrigger
                  render={<Button className="ml-auto" variant="destructive" disabled={saving} />}
                >
                  <Trash2Icon data-icon="inline-start" />
                  Delete
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete {opened.name}?</AlertDialogTitle>
                    <AlertDialogDescription>
                      This removes routines/{opened.name} and every file in it.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={() => void remove(opened)}>
                      Delete
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </div>
        </>
      )}
    </div>
  )
}
