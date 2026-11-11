import type { Clock, InstanceClient, PermissionMode } from "@kinby/contract"
import { type Ref, useCallback, useState } from "react"

import { Composer } from "@/components/composer"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Progress, ProgressLabel, ProgressValue } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { useListedThread } from "@/hooks/use-listed-thread"
import { useRead } from "@/hooks/use-read"
import { reason } from "@/lib/operation"
import { contextFill, contextRows, shortTokens, tokens } from "@/lib/stats"

/** Every mode, from the least the instance may do to the most. */
const MODES: { mode: PermissionMode; label: string; hint: string }[] = [
  { mode: "read-only", label: "Read-only", hint: "Writes are denied" },
  { mode: "ask", label: "Ask", hint: "Writes wait for you" },
  { mode: "auto", label: "Auto", hint: "Writes run, the denylist still applies" },
  { mode: "full-access", label: "Full access", hint: "Everything runs" },
]

/**
 * The composer of a thread: a message starts the next turn, and the foot holds the mode the thread
 * runs in, from the thread list the header reads, and the thread's context size, which opens what
 * fills it. Every kept list is read again after a change of mode so the sidebar shows it too.
 */
export function ThreadComposer({
  ref,
  client,
  clock,
  threadId,
  name,
  running,
  contextSize,
  modelWindow,
}: {
  ref?: Ref<HTMLTextAreaElement>
  client: Pick<InstanceClient, "call">
  clock: Clock
  threadId: string
  name: string
  running: boolean
  /** None before the thread's first model call, and then the foot shows none. */
  contextSize: number | undefined
  modelWindow: number | undefined
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
      {contextSize !== undefined && (
        <Popover>
          <PopoverTrigger
            render={<Button variant="ghost" size="sm" aria-label="What fills the context" />}
            className="max-w-56 min-w-0 flex-1"
          >
            <ContextSize contextSize={contextSize} modelWindow={modelWindow} />
          </PopoverTrigger>
          <PopoverContent align="end" side="top" className="w-80">
            <ContextBreakdown client={client} clock={clock} threadId={threadId} />
          </PopoverContent>
        </Popover>
      )}
    </Composer>
  )
}

/** The context size on a bar that fills toward the model's window, or alone with no window. */
function ContextSize({
  contextSize,
  modelWindow,
}: {
  contextSize: number
  modelWindow: number | undefined
}) {
  if (modelWindow === undefined) {
    return (
      <span className="text-xs text-muted-foreground tabular-nums">
        Context {shortTokens(contextSize)}
      </span>
    )
  }
  return (
    <Progress
      value={contextFill(contextSize, modelWindow)}
      getAriaValueText={() => `${tokens(contextSize)} of ${tokens(modelWindow)} tokens`}
      size="inline"
      className="min-w-0 flex-1"
    >
      <ProgressLabel>Context</ProgressLabel>
      <ProgressValue>
        {() => `${shortTokens(contextSize)} / ${shortTokens(modelWindow)}`}
      </ProgressValue>
    </Progress>
  )
}

/** What fills the thread's context size, read when the panel opens: a row per category. */
function ContextBreakdown({
  client,
  clock,
  threadId,
}: {
  client: Pick<InstanceClient, "call">
  clock: Clock
  threadId: string
}) {
  const read = useCallback(
    () => client.call("thread.context.get", { thread_id: threadId }),
    [client, threadId],
  )
  const { value, failure } = useRead(read, clock)
  return (
    <>
      <PopoverTitle>Context</PopoverTitle>
      {value === undefined ? (
        failure === undefined ? (
          <Spinner aria-label="Reading what fills the context" />
        ) : (
          <p className="text-muted-foreground">{reason(failure)}</p>
        )
      ) : value.rows.length === 0 ? (
        <p className="text-muted-foreground">The breakdown shows once the next turn completes.</p>
      ) : (
        <Table>
          <TableBody>
            {contextRows(value.rows).map((row) => (
              <TableRow key={row.label}>
                <TableCell>{row.label}</TableCell>
                <TableCell className="text-right">{shortTokens(row.tokens)}</TableCell>
                <TableCell className="text-right">{row.share.toFixed(0)}%</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
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
                  {index > allowed ? "Above the ceiling. Raise it in Config." : entry.hint}
                </span>
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
