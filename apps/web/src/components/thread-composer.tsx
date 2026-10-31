import type { InstanceClient, PermissionMode } from "@kinby/contract"
import { type Ref, useState } from "react"

import { Composer } from "@/components/composer"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { useListedThread } from "@/hooks/use-listed-thread"
import { reason } from "@/lib/operation"

/** Every mode, from the least the instance may do to the most. */
const MODES: { mode: PermissionMode; label: string; hint: string }[] = [
  { mode: "read-only", label: "Read-only", hint: "Writes are denied" },
  { mode: "ask", label: "Ask", hint: "Writes wait for you" },
  { mode: "auto", label: "Auto", hint: "Writes run, the denylist still applies" },
  { mode: "full-access", label: "Full access", hint: "Everything runs" },
]

/**
 * The composer of a thread: a message starts the next turn, and the foot holds the mode the thread
 * runs in, from the thread list the header reads. Every kept list is read again after a change of
 * mode so the sidebar shows it too.
 */
export function ThreadComposer({
  ref,
  client,
  threadId,
  name,
  running,
}: {
  ref?: Ref<HTMLTextAreaElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  name: string
  running: boolean
}) {
  const { threads, thread, ceiling } = useListedThread(client, threadId)
  const [failure, setFailure] = useState<string>()
  const setMode = async (mode: PermissionMode) => {
    try {
      await client.call("thread.mode.set", { thread_id: threadId, mode })
      await threads.listKept()
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    }
  }

  return (
    <Composer
      ref={ref}
      label={`Message ${name}`}
      placeholder={running ? `${name} is working…` : `Message ${name}`}
      name={name}
      running={running}
      failure={failure}
      onSend={(message) => client.call("thread.turn.start", { thread_id: threadId, message })}
      onStop={() => client.call("thread.turn.interrupt", { thread_id: threadId })}
    >
      {thread !== undefined && ceiling !== undefined && (
        <ModePicker mode={thread.mode} ceiling={ceiling} onPick={setMode} />
      )}
    </Composer>
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
  onPick: (mode: PermissionMode) => Promise<void>
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
      <SelectContent align="start" alignItemWithTrigger={false} className="w-auto">
        <SelectGroup>
          {MODES.map((entry, index) => (
            <SelectItem key={entry.mode} value={entry.mode} disabled={index > allowed}>
              <span className="flex flex-col">
                {entry.label}
                <span className="text-xs text-muted-foreground">
                  {index > allowed ? "Above the ceiling. Raise it in Configure." : entry.hint}
                </span>
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
