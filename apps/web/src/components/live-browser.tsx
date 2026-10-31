import type { TakeoverFrame, TakeoverPointer, ThreadTakeoverInputCommand } from "@kinby/contract"
import { type MouseEvent, type Ref, useEffect, useImperativeHandle, useRef, useState } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { reason } from "@/lib/operation"

/** The user's mouse or keyboard, as the page takes it. */
type TakeoverInput = ThreadTakeoverInputCommand["input"]

/** A takeover's frames, in order, until cancelled. */
interface Frames extends AsyncIterable<TakeoverFrame> {
  cancel(): void
}

/** Keys a browser reports on their own while they are held, which only change another key. */
const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock"])

/** The input a live browser took, as its owner waits on it. */
export interface LiveBrowserInput {
  /** Settles once every input taken so far has reached the page. */
  drained(): Promise<void>
}

/**
 * A browser's page live, which takes the user's mouse and keyboard. `watch` starts its frames and
 * must keep its identity; `send` passes the page one input. A `disabled` browser takes no input.
 */
export function LiveBrowser({
  ref,
  watch,
  send,
  disabled = false,
}: {
  ref?: Ref<LiveBrowserInput>
  watch: () => Frames
  send: (input: TakeoverInput) => Promise<unknown>
  disabled?: boolean
}) {
  const view = useLiveView(watch)
  const page = useRef<HTMLImageElement>(null)
  const screen = useRef<HTMLDivElement>(null)
  // Input goes one call at a time, so a release never overtakes its press.
  const queue = useRef(Promise.resolve())
  const queued = useRef(0)
  const [failure, setFailure] = useState<string>()
  useImperativeHandle(ref, () => ({ drained: () => queue.current }), [])
  // React listens to the wheel passively, so only a listener of the browser's own keeps the wheel
  // that scrolls the page from also scrolling what holds the browser.
  useEffect(() => {
    const element = screen.current
    if (element === null || disabled) return
    const hold = (event: WheelEvent) => event.preventDefault()
    element.addEventListener("wheel", hold, { passive: false })
    return () => element.removeEventListener("wheel", hold)
  }, [disabled])

  const pass = (input: TakeoverInput) => {
    if (disabled) return
    // A move behind other input is stale by the time it would run.
    if (input.type === "pointer" && input.action === "move" && queued.current > 0) return
    queued.current += 1
    queue.current = queue.current
      .then(() => send(input))
      .then(
        () => setFailure(undefined),
        (error: unknown) => setFailure(reason(error)),
      )
      .finally(() => {
        queued.current -= 1
      })
  }
  const pointer = (action: TakeoverPointer["action"]) => (event: MouseEvent) => {
    const shown = page.current
    if (shown === null || (event.button !== 0 && action !== "move")) return
    const box = shown.getBoundingClientRect()
    pass({
      type: "pointer",
      action,
      x: Math.round(((event.clientX - box.left) * shown.naturalWidth) / box.width),
      y: Math.round(((event.clientY - box.top) * shown.naturalHeight) / box.height),
    })
  }

  return (
    <>
      {/* A remote screen, which ARIA marks as an application: it passes the page every key, Tab
          included, and the mouse. */}
      {/* oxlint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
      <div
        ref={screen}
        role="application"
        aria-label="The browser"
        tabIndex={0}
        className="overflow-hidden rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        onMouseDown={pointer("down")}
        onMouseUp={pointer("up")}
        onMouseMove={pointer("move")}
        onWheel={(event) => pass({ type: "wheel", delta_x: event.deltaX, delta_y: event.deltaY })}
        onContextMenu={(event) => event.preventDefault()}
        onKeyDown={(event) => {
          if (MODIFIERS.has(event.key) || event.nativeEvent.isComposing) return
          event.preventDefault()
          const held = [
            event.ctrlKey && "Control",
            event.altKey && "Alt",
            event.metaKey && "Meta",
            // A character already reads as shifted.
            event.shiftKey && event.key.length > 1 && "Shift",
          ].filter((key) => key !== false)
          pass({ type: "key", key: [...held, event.key].join("+") })
        }}
      >
        {view.frame === undefined ? (
          <Skeleton aria-label="Loading the browser" className="aspect-video w-full" />
        ) : (
          <img
            ref={page}
            src={`data:image/jpeg;base64,${view.frame}`}
            alt="The browser's page"
            draggable={false}
            className="w-full select-none"
          />
        )}
      </div>
      {/* oxlint-enable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
      {(failure ?? view.failure) !== undefined && (
        <p role="alert" className="text-sm text-destructive">
          {failure ?? view.failure}
        </p>
      )}
    </>
  )
}

/** The newest frame of the live view, base64-encoded, or why the view stopped. */
function useLiveView(watch: () => Frames): { frame?: string; failure?: string } {
  const [frame, setFrame] = useState<string>()
  const [failure, setFailure] = useState<string>()
  useEffect(() => {
    const frames = watch()
    let following = true
    const read = async () => {
      try {
        for await (const { data } of frames) {
          if (following) setFrame(data)
        }
      } catch (error) {
        if (following) setFailure(reason(error))
      }
    }
    void read()
    return () => {
      following = false
      frames.cancel()
    }
  }, [watch])
  return { frame, failure }
}
