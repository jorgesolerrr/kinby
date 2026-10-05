import type { InstanceClient, PermissionMode, ThreadSummary } from "@kinby/contract"
import { useEffect, useRef, useState, useSyncExternalStore } from "react"

import { Button } from "@/components/ui/button"
import { Field, FieldError } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import { threadList, threadTitle } from "@/lib/thread-list"
import { ArchiveIcon, ArchiveRestoreIcon, PencilIcon } from "lucide-react"

/** Every mode, from the least the instance may do to the most. */
const MODES: { mode: PermissionMode; label: string; hint: string }[] = [
  { mode: "read-only", label: "Read-only", hint: "Writes are denied" },
  { mode: "ask", label: "Ask", hint: "Writes wait for you" },
  { mode: "auto", label: "Auto", hint: "Writes run, the denylist still applies" },
  { mode: "full-access", label: "Full access", hint: "Everything runs" },
]

/**
 * The thread's title, which a click renames, the mode it runs in, and archive or unarchive. They
 * come from the instance's full thread list, so an archived thread keeps its header, and the lists
 * are read again after each change so the sidebar shows it too.
 */
export function ThreadHeader({
  client,
  threadId,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  threadId: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const threads = threadList(client, "all")
  const listed = useSyncExternalStore(threads.onChange, threads.view)
  const thread = listed?.threads.find((summary) => summary.id === threadId)
  const missing = thread === undefined
  const [failure, setFailure] = useState<string>()
  // The sidebar lists the threads, but at phone width it is closed and nothing does. The client
  // refuses a call before its socket is up, so the header waits for it. A header that cannot list
  // the threads stays empty, and the transcript under it does not depend on it.
  useEffect(() => {
    if (connected && missing) threads.list().catch(() => {})
  }, [threads, threadId, connected, missing])

  if (listed === undefined || thread === undefined) return null
  /** Make a change, then list the threads again. Resolves to whether the instance took it. */
  const change = async (call: () => Promise<unknown>) => {
    try {
      await call()
      await threads.list()
      setFailure(undefined)
      return true
    } catch (error) {
      setFailure(reason(error))
      return false
    }
  }

  return (
    <Field data-invalid={failure !== undefined || undefined} className="mx-4 my-2 w-auto">
      <div className="flex items-center gap-2">
        <ThreadTitle
          thread={thread}
          invalid={failure !== undefined}
          onRename={(title) =>
            change(() => client.call("thread.rename", { thread_id: threadId, title }))
          }
        />
        <ModePicker
          mode={thread.mode}
          ceiling={listed.ceiling}
          onPick={(mode) =>
            change(() => client.call("thread.mode.set", { thread_id: threadId, mode }))
          }
        />
        <ArchiveButton
          archived={thread.archived}
          onToggle={() =>
            change(() =>
              client.call(thread.archived ? "thread.unarchive" : "thread.archive", {
                thread_id: threadId,
              }),
            )
          }
        />
      </div>
      {failure !== undefined && <FieldError>{failure}</FieldError>}
    </Field>
  )
}

/**
 * Enter renames the thread, and Escape or leaving the field keeps the title it had. While the rename
 * is out, the field holds the new title and takes no keys. A field that closes with focus in it
 * gives focus back to the title.
 */
function ThreadTitle({
  thread,
  invalid,
  onRename,
}: {
  thread: ThreadSummary
  invalid: boolean
  onRename: (title: string) => Promise<boolean>
}) {
  const [draft, setDraft] = useState<string>()
  const [renaming, setRenaming] = useState(false)
  const field = useRef<HTMLInputElement>(null)
  const titleButton = useRef<HTMLButtonElement>(null)
  const refocus = useRef(false)
  const editing = draft !== undefined
  useEffect(() => {
    if (editing) field.current?.focus()
    else if (refocus.current) titleButton.current?.focus()
    refocus.current = false
  }, [editing])

  if (draft === undefined) {
    return (
      // No width of its own, so a long title truncates instead of widening the panel.
      <h1 className="w-0 flex-1">
        <Button
          ref={titleButton}
          variant="ghost"
          className="max-w-full"
          onClick={() => setDraft(thread.title ?? "")}
        >
          <span className="truncate">{threadTitle(thread)}</span>
          <PencilIcon data-icon="inline-end" />
        </Button>
      </h1>
    )
  }
  const close = () => {
    refocus.current = document.activeElement === field.current
    setDraft(undefined)
  }
  const rename = async (title: string) => {
    setRenaming(true)
    const renamed = await onRename(title)
    setRenaming(false)
    if (renamed) close()
  }
  return (
    <>
      <Input
        aria-label="Thread title"
        aria-invalid={invalid || undefined}
        ref={field}
        className="flex-1"
        value={draft}
        readOnly={renaming}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (!renaming) setDraft(undefined)
        }}
        onKeyDown={(event) => {
          if (renaming) return
          if (event.key === "Escape") close()
          if (event.key !== "Enter" || event.nativeEvent.isComposing) return
          event.preventDefault()
          const title = draft.trim()
          if (title === "" || title === thread.title) return close()
          void rename(title)
        }}
      />
      {renaming && <Spinner aria-label="Renaming" />}
    </>
  )
}

/** The instance refuses a mode above its ceiling, so the picker disables each one and says why. */
function ModePicker({
  mode,
  ceiling,
  onPick,
}: {
  mode: PermissionMode
  ceiling: PermissionMode
  onPick: (mode: PermissionMode) => Promise<boolean>
}) {
  const [changing, setChanging] = useState(false)
  const allowed = MODES.findIndex((entry) => entry.mode === ceiling)
  const pick = async (picked: PermissionMode) => {
    setChanging(true)
    await onPick(picked)
    setChanging(false)
  }
  return (
    <Select<PermissionMode>
      value={mode}
      // A disabled trigger drops focus, and a read-only one keeps it and refuses the next pick.
      readOnly={changing}
      onValueChange={(picked) => picked !== null && picked !== mode && void pick(picked)}
    >
      <SelectTrigger size="sm" aria-label="Mode" className="shrink-0">
        <SelectValue>
          {(value: PermissionMode) => MODES.find((entry) => entry.mode === value)?.label}
        </SelectValue>
        {changing && <Spinner aria-label="Changing the mode" />}
      </SelectTrigger>
      <SelectContent align="end" alignItemWithTrigger={false} className="w-auto">
        <SelectGroup>
          {MODES.map((entry, index) => (
            <SelectItem key={entry.mode} value={entry.mode} disabled={index > allowed}>
              <span className="flex flex-col">
                {entry.label}
                <span className="text-xs text-muted-foreground">
                  {index > allowed ? "Above this instance's ceiling" : entry.hint}
                </span>
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

/** Archive puts the thread away, out of the sidebar; unarchive clears that, and the sidebar rule decides whether it shows again. */
function ArchiveButton({
  archived,
  onToggle,
}: {
  archived: boolean
  onToggle: () => Promise<boolean>
}) {
  const [changing, setChanging] = useState(false)
  const toggle = async () => {
    if (changing) return
    setChanging(true)
    await onToggle()
    setChanging(false)
  }
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={archived ? "Unarchive" : "Archive"}
      onClick={() => void toggle()}
    >
      {changing ? <Spinner /> : archived ? <ArchiveRestoreIcon /> : <ArchiveIcon />}
    </Button>
  )
}
