import type { Clock, InstanceClient, ManifestCompaction, PermissionMode } from "@kinby/contract"
import { type Ref, useCallback, useState } from "react"

import { Composer } from "@/components/composer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
import {
  contextAgainst,
  contextFill,
  contextLimit,
  contextRows,
  effectiveTrigger,
  shortTokens,
  tokens,
} from "@/lib/stats"

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
 * fills it and compacts the thread. Every kept list is read again after a change of mode so the
 * sidebar shows it too.
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
        <ContextBar
          client={client}
          clock={clock}
          threadId={threadId}
          running={running}
          contextSize={contextSize}
          modelWindow={modelWindow}
        />
      )}
    </Composer>
  )
}

/**
 * The context size on a bar that fills toward the effective compaction trigger, or the model's
 * window with automatic compaction off. It opens the panel, which states the size against both,
 * says what fills it, and compacts the thread.
 */
function ContextBar({
  client,
  clock,
  threadId,
  running,
  contextSize,
  modelWindow,
}: {
  client: Pick<InstanceClient, "call">
  clock: Clock
  threadId: string
  running: boolean
  contextSize: number
  modelWindow: number | undefined
}) {
  const readManifest = useCallback(() => client.call("manifest.get", {}), [client])
  const compaction = useRead(readManifest, clock).value?.values.compaction
  return (
    <Popover>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" aria-label="What fills the context" />}
        className="max-w-56 min-w-0 flex-1"
      >
        <ContextSize contextSize={contextSize} limit={contextLimit(compaction, modelWindow)} />
      </PopoverTrigger>
      <PopoverContent align="end" side="top" className="w-80">
        <PopoverTitle>Context</PopoverTitle>
        <ContextLimits
          contextSize={contextSize}
          compaction={compaction}
          modelWindow={modelWindow}
        />
        <ContextBreakdown client={client} clock={clock} threadId={threadId} />
        <CompactThread client={client} threadId={threadId} running={running} />
      </PopoverContent>
    </Popover>
  )
}

/** The context size on a bar that fills toward `limit`, or alone with no limit known. */
function ContextSize({ contextSize, limit }: { contextSize: number; limit: number | undefined }) {
  if (limit === undefined) {
    return (
      <span className="text-xs text-muted-foreground tabular-nums">
        Context {shortTokens(contextSize)}
      </span>
    )
  }
  return (
    <Progress
      value={contextFill(contextSize, limit)}
      getAriaValueText={() => `${tokens(contextSize)} of ${tokens(limit)} tokens`}
      size="inline"
      className="min-w-0 flex-1"
    >
      <ProgressLabel>Context</ProgressLabel>
      <ProgressValue>{() => `${shortTokens(contextSize)} / ${shortTokens(limit)}`}</ProgressValue>
    </Progress>
  )
}

/** The context size against the effective compaction trigger and the model's window. */
function ContextLimits({
  contextSize,
  compaction,
  modelWindow,
}: {
  contextSize: number
  /** None until the manifest is read, and then the trigger is left out. */
  compaction: ManifestCompaction | undefined
  modelWindow: number | undefined
}) {
  const trigger = effectiveTrigger(compaction, modelWindow)
  return (
    <Table>
      <TableBody>
        {compaction !== undefined && (
          <TableRow>
            <TableCell>Compaction trigger</TableCell>
            <TableCell className="text-right">
              {trigger === undefined
                ? "Off"
                : trigger > 0
                  ? contextAgainst(contextSize, trigger)
                  : "Every turn"}
            </TableCell>
          </TableRow>
        )}
        <TableRow>
          <TableCell>Model's window</TableCell>
          <TableCell className="text-right">
            {modelWindow === undefined ? "Unknown" : contextAgainst(contextSize, modelWindow)}
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>
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
  return value === undefined ? (
    failure === undefined ? (
      <Spinner aria-label="Reading what fills the context" />
    ) : (
      <p className="text-muted-foreground">{reason(failure)}</p>
    )
  ) : value.rows.length === 0 ? (
    <p className="text-muted-foreground">Nothing is measured until the next model call.</p>
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
  )
}

/**
 * Compacts the thread, with an optional line of focus for the summary to keep. A running turn
 * holds the thread, so it waits for the turn to end.
 */
function CompactThread({
  client,
  threadId,
  running,
}: {
  client: Pick<InstanceClient, "call">
  threadId: string
  running: boolean
}) {
  const [focus, setFocus] = useState("")
  const [compacting, setCompacting] = useState(false)
  const [failure, setFailure] = useState<string>()
  const compact = async () => {
    setCompacting(true)
    try {
      await client.call("thread.compact", { thread_id: threadId, focus: focus.trim() || null })
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    }
    setCompacting(false)
  }
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        void compact()
      }}
    >
      <div className="flex gap-2">
        <Input
          aria-label="Focus"
          placeholder="Focus (optional)"
          value={focus}
          onChange={(event) => setFocus(event.target.value)}
        />
        <Button type="submit" size="sm" disabled={running || compacting}>
          {compacting && <Spinner data-icon="inline-start" />}
          Compact
        </Button>
      </div>
      {failure !== undefined && <p className="text-destructive">{failure}</p>}
    </form>
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
