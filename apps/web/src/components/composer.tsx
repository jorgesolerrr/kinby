import { type ReactNode, useEffect, useRef, useState } from "react"

import { Field, FieldDescription, FieldError } from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { takeFocus } from "@/lib/focus"
import { reason } from "@/lib/operation"
import { CornerDownLeftIcon, SquareIcon } from "lucide-react"

/**
 * One box where the user writes to an instance: the text, then a foot with `children` and Send.
 * Enter sends, and Shift+Enter starts a new line. While a turn runs, Send becomes Stop, and Escape
 * stops it too. Under the box, a note names `name`, the instance the message goes to.
 */
export function Composer({
  label,
  placeholder,
  name,
  disabled = false,
  connected = true,
  running = false,
  failure,
  onSend,
  onStop,
  children,
}: {
  label: string
  placeholder: string
  name: string | undefined
  disabled?: boolean
  /** Whether the instance can take a message now. */
  connected?: boolean
  running?: boolean
  /** Why the last change made from the foot failed. */
  failure?: string
  onSend: (message: string) => Promise<unknown>
  onStop?: () => Promise<unknown>
  /** What the foot holds before Send. */
  children?: ReactNode
}) {
  const [message, setMessage] = useState("")
  const [sending, setSending] = useState(false)
  const [sendFailure, setSendFailure] = useState<string>()
  const field = useRef<HTMLTextAreaElement>(null)
  const text = message.trim()
  // The composer shows when the app opens on the home screen, when a thread opens, and when an
  // answered approval gives way to it.
  useEffect(() => takeFocus(field.current), [])

  const send = async () => {
    if (text === "" || sending || running || !connected) return
    setSending(true)
    try {
      await onSend(text)
      setMessage("")
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
      <InputGroup>
        <InputGroupTextarea
          ref={field}
          variant="prompt"
          className="max-h-48 min-h-14"
          aria-label={label}
          placeholder={placeholder}
          disabled={disabled}
          value={message}
          aria-invalid={sendFailure !== undefined || undefined}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && running && !event.nativeEvent.isComposing) {
              event.preventDefault()
              void stop()
              return
            }
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            void send()
          }}
        />
        <InputGroupAddon align="block-end">
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
              aria-disabled={text === "" || sending || !connected}
              onClick={() => void send()}
            >
              Send
              <CornerDownLeftIcon data-icon="inline-end" />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
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
