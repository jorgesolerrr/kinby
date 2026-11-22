import type { SkillSummary } from "@kinby/contract"
import {
  type ReactNode,
  type Ref,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react"

import { ReferencedText } from "@/components/referenced-text"
import { UploadChips } from "@/components/upload-chips"
import { Command, CommandItem, CommandList } from "@/components/ui/command"
import { Field, FieldDescription, FieldError } from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { useUploads } from "@/hooks/use-uploads"
import { type Draft, draft, EMPTY_DRAFT, keepDraft } from "@/lib/drafts"
import { takeFocus } from "@/lib/focus"
import { reason } from "@/lib/operation"
import { carried, slashWord } from "@/lib/references"
import { loadable } from "@/lib/skills"
import { CornerDownLeftIcon, PaperclipIcon, SquareIcon } from "lucide-react"

/**
 * One box where the user writes to an instance: the text, then a foot with `children` and Send.
 * Enter sends, and Shift+Enter starts a new line. While a turn runs, Send becomes Stop, and Escape
 * stops it too. Under the box, a note names `name`, the instance the message goes to. The message
 * stays in the box until `onSend` resolves, and when it resolves `false`. An unsent message is kept
 * as the draft under `draftKey`, and the box opens on it.
 *
 * A `/` that starts a word opens a menu of the skills `listSkills` reads, and a pick puts a skill
 * reference in the message. The arrow keys move in the menu, Enter or Tab picks, and Escape closes.
 *
 * With `onUpload`, files picked from the foot or dropped on the box, and images pasted into it,
 * upload at once and show as chips over the text, kept with the draft. Send waits until every one is uploaded, and a message
 * may then be only files. A file removed before sending stays wherever it was uploaded.
 */
export function Composer({
  ref,
  draftKey,
  label,
  placeholder,
  name,
  disabled = false,
  connected = true,
  sendLabel = "Send",
  running = false,
  failure,
  onSend,
  onUpload,
  listSkills,
  onStop,
  children,
}: {
  /** The text area, for a page that moves focus back to it. */
  ref?: Ref<HTMLTextAreaElement>
  /** Where the unsent message is kept: a thread's id, or a key of the screen's own. */
  draftKey: string
  label: string
  placeholder: string
  name: string | undefined
  disabled?: boolean
  /** Whether the instance can take a message now. */
  connected?: boolean
  sendLabel?: string
  running?: boolean
  /** Why the last change made from the foot failed. */
  failure?: string
  /** Sends the message with the names of the skills it references and the paths of its files. */
  onSend: (message: string, skills: string[], uploads: string[]) => Promise<unknown>
  /** Uploads a file added to the message, and resolves to its path. None takes no files. */
  onUpload?: (file: File) => Promise<string>
  /** The instance's skills, read once a `/` starts a word. None leaves the menu closed. */
  listSkills?: () => Promise<SkillSummary[]>
  onStop?: () => Promise<unknown>
  /** What the foot holds before Send. */
  children?: ReactNode
}) {
  const [message, setMessage] = useState(() => draft(draftKey))
  const [caret, setCaret] = useState(message.text.length)
  const [sending, setSending] = useState(false)
  const [sendFailure, setSendFailure] = useState<string>()
  const field = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  useImperativeHandle(ref, () => field.current as HTMLTextAreaElement, [])
  const text = message.text.trim()
  const { uploads, add, remove, sent: sentUploads } = useUploads(draftKey, onUpload)
  const unsendable =
    (text === "" && uploads.length === 0) || uploads.some((upload) => upload.status !== "uploaded")
  // The composer shows when the app opens on the home screen, and when a thread opens.
  useEffect(() => takeFocus(field.current), [])
  // A pick writes the box, so the caret goes after the reference it put in.
  const picked = useRef<number>(undefined)
  useLayoutEffect(() => {
    if (picked.current === undefined) return
    field.current?.setSelectionRange(picked.current, picked.current)
    picked.current = undefined
  })

  const slash = slashWord(message.text, caret, message.references)
  const [listed, setListed] = useState<{ from: typeof listSkills; skills: SkillSummary[] }>()
  const skills = listed !== undefined && listed.from === listSkills ? listed.skills : undefined
  const reading = slash !== undefined && listSkills !== undefined && skills === undefined
  useEffect(() => {
    if (!reading) return
    let current = true
    listSkills().then(
      (read) => current && setListed({ from: listSkills, skills: read }),
      // A list that can't be read leaves the menu closed, and the message sends as typed.
      () => {},
    )
    return () => {
      current = false
    }
  }, [reading, listSkills])
  const [closedAt, setClosedAt] = useState<number>()
  const rows =
    slash === undefined || slash.at === closedAt || skills === undefined
      ? []
      : loadable(skills, slash.query)
  const [highlighted, setHighlighted] = useState<string>()
  const active = rows.find((row) => row.name === highlighted) ?? rows.at(0)

  const write = (next: Draft, at: number) => {
    setMessage(next)
    setCaret(at)
    keepDraft(draftKey, next)
  }
  const pick = (name: string) => {
    if (slash === undefined) return
    const after = message.text.slice(caret)
    const token = after === "" ? `/${name} ` : `/${name}`
    const next = message.text.slice(0, slash.at) + token + after
    const at = slash.at + token.length
    write(
      {
        text: next,
        references: [
          ...carried(message.references, message.text, next, at),
          { name, at: slash.at },
        ],
      },
      at,
    )
    picked.current = at
    setHighlighted(undefined)
  }

  const send = async () => {
    if (unsendable || sending || running || !connected) return
    setSending(true)
    const sent = message
    const names = sent.references.toSorted((a, b) => a.at - b.at).map((reference) => reference.name)
    const paths = uploads.flatMap((upload) => (upload.status === "uploaded" ? [upload.path] : []))
    try {
      if ((await onSend(text, [...new Set(names)], paths)) !== false) {
        // The box may have changed while the send was out, here or in a composer opened again.
        setMessage((current) => (current === sent ? EMPTY_DRAFT : current))
        setClosedAt(undefined)
        if (draft(draftKey) === sent) keepDraft(draftKey, EMPTY_DRAFT)
        sentUploads(uploads)
      }
      setSendFailure(undefined)
    } catch (error) {
      setSendFailure(reason(error))
    } finally {
      setSending(false)
    }
  }
  const stop = () =>
    onStop?.().then(
      () => setSendFailure(undefined),
      (error: unknown) => setSendFailure(reason(error)),
    )

  return (
    <Field data-invalid={sendFailure !== undefined || failure !== undefined || undefined}>
      {/* A long draft scrolls in the box instead of pushing the transcript away. */}
      <InputGroup
        variant="raised"
        onDragOver={(event) => {
          if (onUpload !== undefined) event.preventDefault()
        }}
        onDrop={(event) => {
          if (onUpload === undefined) return
          event.preventDefault()
          add(event.dataTransfer.files)
        }}
      >
        {uploads.length > 0 && (
          <InputGroupAddon align="block-start">
            <UploadChips uploads={uploads} onRemove={remove} />
          </InputGroupAddon>
        )}
        {rows.length > 0 && (
          <div className="absolute bottom-full left-0 z-50 mb-2 w-full">
            <Command
              variant="popup"
              shouldFilter={false}
              value={active?.name}
              onValueChange={setHighlighted}
              // A pointer press on a row would take focus from the box, where the menu's keys are read.
              onMouseDown={(event) => event.preventDefault()}
            >
              <CommandList aria-label="Skills">
                {rows.map((row) => (
                  <CommandItem key={row.name} value={row.name} onSelect={pick}>
                    <span className="flex min-w-0 flex-col">
                      <span className="font-mono">/{row.name}</span>
                      <span className="truncate text-xs font-normal text-muted-foreground">
                        {row.description}
                      </span>
                    </span>
                  </CommandItem>
                ))}
              </CommandList>
            </Command>
          </div>
        )}
        {/* The references show through the box from a copy of its text laid out behind it. */}
        <div className="grid w-full">
          {message.references.length > 0 && (
            <div
              ref={mirror}
              aria-hidden
              className="col-start-1 row-start-1 max-h-48 overflow-hidden border border-transparent px-3 py-2.5 text-base wrap-break-word whitespace-pre-wrap text-transparent md:text-sm"
            >
              <ReferencedText text={message.text} references={message.references} />
            </div>
          )}
          <InputGroupTextarea
            ref={field}
            className="col-start-1 row-start-1 max-h-48 min-h-14"
            aria-label={label}
            placeholder={placeholder}
            disabled={disabled}
            value={message.text}
            aria-invalid={sendFailure !== undefined || undefined}
            onChange={(event) => {
              const next = event.target.value
              const at = event.target.selectionEnd
              write(
                { text: next, references: carried(message.references, message.text, next, at) },
                at,
              )
              if (slashWord(next, at, []) === undefined) setClosedAt(undefined)
            }}
            onPaste={(event) => {
              const images = Array.from(event.clipboardData.files).filter((file) =>
                file.type.startsWith("image/"),
              )
              if (onUpload === undefined || images.length === 0) return
              event.preventDefault()
              add(images)
            }}
            onSelect={(event) => setCaret(event.currentTarget.selectionEnd)}
            onScroll={(event) => {
              if (mirror.current !== null) mirror.current.scrollTop = event.currentTarget.scrollTop
            }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return
              if (active !== undefined && slash !== undefined) {
                const at = rows.indexOf(active)
                const keys: Record<string, () => void> = {
                  ArrowDown: () => setHighlighted(rows[Math.min(at + 1, rows.length - 1)].name),
                  ArrowUp: () => setHighlighted(rows[Math.max(at - 1, 0)].name),
                  Enter: () => pick(active.name),
                  Tab: () => pick(active.name),
                  Escape: () => setClosedAt(slash.at),
                }
                if (event.key in keys && !event.shiftKey) {
                  event.preventDefault()
                  keys[event.key]()
                  return
                }
              }
              if (event.key === "Escape" && running) {
                event.preventDefault()
                void stop()
                return
              }
              if (event.key !== "Enter" || event.shiftKey) return
              event.preventDefault()
              void send()
            }}
          />
        </div>
        <InputGroupAddon align="block-end">
          {onUpload !== undefined && (
            <InputGroupButton
              size="icon-sm"
              aria-label="Add files"
              onClick={() => picker.current?.click()}
            >
              <PaperclipIcon />
            </InputGroupButton>
          )}
          {children}
          {running ? (
            <InputGroupButton
              size="sm"
              variant="outline"
              className="ml-auto"
              onClick={() => void stop()}
            >
              <SquareIcon data-icon="inline-start" className="fill-current" />
              Stop
              <kbd aria-hidden className="font-mono text-xs text-muted-foreground">
                Esc
              </kbd>
            </InputGroupButton>
          ) : (
            <InputGroupButton
              size="sm"
              variant="default"
              className="ml-auto"
              // aria-disabled, so an empty draft leaves the field looking ready to type in.
              aria-disabled={unsendable || sending || !connected}
              onClick={() => void send()}
            >
              {sendLabel}
              <CornerDownLeftIcon data-icon="inline-end" />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
      {/* Outside the group, whose addons focus the first input in it. */}
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        aria-label="Files"
        onChange={(event) => {
          add(event.currentTarget.files ?? [])
          // So the same file can be picked again.
          event.currentTarget.value = ""
        }}
      />
      {sendFailure !== undefined && <FieldError>{sendFailure}</FieldError>}
      {failure !== undefined && <FieldError>{failure}</FieldError>}
      {name !== undefined && (
        <FieldDescription className="text-center">
          {name} can make mistakes. It asks before anything it can't undo.
        </FieldDescription>
      )}
    </Field>
  )
}
