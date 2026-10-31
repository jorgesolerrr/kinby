import type { InstanceClient } from "@kinby/contract"
import { useEffect, useRef, useState } from "react"

import { Field, FieldError } from "@/components/ui/field"
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
 * Enter sends the message as a new turn, and Shift+Enter starts a new line. While a turn runs,
 * Send becomes Stop, and Escape stops it too.
 */
export function Composer({
  client,
  threadId,
  name,
  running,
}: {
  client: Pick<InstanceClient, "call">
  threadId: string
  name: string
  running: boolean
}) {
  const [message, setMessage] = useState("")
  const [sending, setSending] = useState(false)
  const [failure, setFailure] = useState<string>()
  const field = useRef<HTMLTextAreaElement>(null)
  const text = message.trim()
  // The composer shows when a thread opens and when an answered approval gives way to it.
  useEffect(() => takeFocus(field.current), [])

  const send = async () => {
    if (text === "" || sending || running) return
    setSending(true)
    try {
      await client.call("thread.turn.start", { thread_id: threadId, message: text })
      setMessage("")
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setSending(false)
    }
  }
  const stop = () =>
    client.call("thread.turn.interrupt", { thread_id: threadId }).then(
      () => setFailure(undefined),
      (error: unknown) => setFailure(reason(error)),
    )

  return (
    <Field data-invalid={failure !== undefined || undefined}>
      {/* Send sits inside the field, and a long draft scrolls in it instead of pushing the
          transcript away. */}
      <InputGroup>
        <InputGroupAddon align="inline-start">
          <span aria-hidden className="font-mono text-muted-foreground">
            ›
          </span>
        </InputGroupAddon>
        <InputGroupTextarea
          ref={field}
          variant="prompt"
          className="max-h-48"
          aria-label={`Message ${name}`}
          placeholder={running ? `${name} is working…` : `Message ${name}`}
          value={message}
          aria-invalid={failure !== undefined || undefined}
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
        <InputGroupAddon align="inline-end">
          {running ? (
            <InputGroupButton size="sm" variant="outline" onClick={() => void stop()}>
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
              // aria-disabled, so an empty draft leaves the field looking ready to type in.
              aria-disabled={text === "" || sending}
              onClick={() => void send()}
            >
              Send
              <CornerDownLeftIcon data-icon="inline-end" />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
      {failure !== undefined && <FieldError>{failure}</FieldError>}
    </Field>
  )
}
