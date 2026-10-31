import type { InstanceClient, ThreadSummary } from "@kinby/contract"
import { useEffect, useRef, useState, useSyncExternalStore } from "react"

import { ArchiveButton } from "@/components/archive-button"
import { Button } from "@/components/ui/button"
import { Field, FieldError } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { useListedThread } from "@/hooks/use-listed-thread"
import { reason } from "@/lib/operation"
import { threadTitle } from "@/lib/thread-list"
import { PencilIcon } from "lucide-react"

/**
 * The thread's title, which a click renames, and archive or unarchive. They come from the sidebar's
 * thread list, or from the instance's full list for a thread outside it, so an archived thread
 * keeps its header. Every kept list is read again after each change so the sidebar shows it too.
 */
export function ThreadHeader({
  client,
  threadId,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  threadId: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const { threads, thread } = useListedThread(client, threadId)
  const missing = thread === undefined
  const [failure, setFailure] = useState<string>()
  // The sidebar lists the threads, but at phone width it is closed and nothing does. The client
  // refuses a call before its socket is up, so the header waits for it. A header that cannot list
  // the threads stays empty, and the transcript under it does not depend on it. The composer's mode
  // waits on the same list.
  useEffect(() => {
    if (connected && missing) threads.list().catch(() => {})
  }, [threads, threadId, connected, missing])

  if (thread === undefined) return null
  /** Make a change, then list the threads again. Resolves to whether the instance took it. */
  const change = async (call: () => Promise<unknown>) => {
    try {
      await call()
      await threads.listKept()
      setFailure(undefined)
      return true
    } catch (error) {
      setFailure(reason(error))
      return false
    }
  }

  return (
    // Sits on the transcript's column, so the title lines up with the turns under it.
    <div className="px-4">
      <Field
        data-invalid={failure !== undefined || undefined}
        className="mx-auto my-2 w-full max-w-3xl"
      >
        <div className="flex items-center gap-2">
          <ThreadTitle
            thread={thread}
            invalid={failure !== undefined}
            onRename={(title) =>
              change(() => client.call("thread.rename", { thread_id: threadId, title }))
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
    </div>
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
          title={threadTitle(thread)}
          onClick={() => setDraft(thread.title ?? "")}
        >
          <span className="truncate font-heading text-base font-semibold">
            {threadTitle(thread)}
          </span>
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
