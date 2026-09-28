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
import { PencilIcon } from "lucide-react"

/** Every mode, from the least the instance may do to the most. */
const MODES: { mode: PermissionMode; label: string; hint: string }[] = [
  { mode: "read-only", label: "Read-only", hint: "Writes are denied" },
  { mode: "ask", label: "Ask", hint: "Writes wait for you" },
  { mode: "auto", label: "Auto", hint: "Writes run, the denylist still applies" },
  { mode: "full-access", label: "Full access", hint: "Everything runs" },
]

/**
 * The thread's title, which a click renames, and the mode it runs in. Both come from the instance's
 * thread list, which is read again after each change so the sidebar shows it too.
 */
export function ThreadHeader({
  client,
  threadId,
}: {
  client: Pick<InstanceClient, "call">
  threadId: string
}) {
  const threads = threadList(client)
  const listed = useSyncExternalStore(threads.onChange, threads.view)
  const thread = listed?.threads.find((summary) => summary.id === threadId)
  const [failure, setFailure] = useState<string>()

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
    <Field data-invalid={failure !== undefined || undefined} className="mx-4 my-2">
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
      </div>
      {failure !== undefined && <FieldError>{failure}</FieldError>}
    </Field>
  )
}

/**
 * Enter renames the thread, and Escape or leaving the field keeps the title it had. While the rename
 * is out, the field holds the new title and takes no keys.
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
  const editing = draft !== undefined
  useEffect(() => {
    if (editing) field.current?.focus()
  }, [editing])

  if (draft === undefined) {
    return (
      <h1 className="min-w-0 flex-1">
        <Button variant="ghost" className="max-w-full" onClick={() => setDraft(thread.title ?? "")}>
          <span className="truncate">{threadTitle(thread)}</span>
          <PencilIcon data-icon="inline-end" />
        </Button>
      </h1>
    )
  }
  const rename = async (title: string) => {
    setRenaming(true)
    const renamed = await onRename(title)
    setRenaming(false)
    if (renamed) setDraft(undefined)
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
          if (event.key === "Escape") setDraft(undefined)
          if (event.key !== "Enter" || event.nativeEvent.isComposing) return
          event.preventDefault()
          const title = draft.trim()
          if (title === "" || title === thread.title) return setDraft(undefined)
          void rename(title)
        }}
      />
      {renaming && <Spinner aria-label="Renaming" />}
    </>
  )
}

/** The instance refuses a mode above its ceiling, so the picker offers none. */
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
      disabled={changing}
      onValueChange={(picked) => picked !== null && picked !== mode && void pick(picked)}
    >
      <SelectTrigger size="sm" aria-label="Mode">
        <SelectValue>
          {(value: PermissionMode) => MODES.find((entry) => entry.mode === value)?.label}
        </SelectValue>
        {changing && <Spinner aria-label="Changing the mode" />}
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {MODES.map((entry, index) => (
            <SelectItem key={entry.mode} value={entry.mode} disabled={index > allowed}>
              {entry.label}
              <span className="text-muted-foreground">{entry.hint}</span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
