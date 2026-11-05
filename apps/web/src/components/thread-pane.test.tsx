import { fireEvent, render, screen } from "@testing-library/react"
import { FileTextIcon, GlobeIcon } from "lucide-react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { type OpenPane, ThreadSplit } from "@/components/thread-pane"

// jsdom lays nothing out, so it has no ResizeObserver, and every element is 0px wide. The split
// sizes its panels only once they have a width.
window.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe("the split beside a thread", () => {
  const offsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth")
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get: () => 1000,
    })
  })
  afterEach(() => {
    if (offsetWidth !== undefined)
      Object.defineProperty(HTMLElement.prototype, "offsetWidth", offsetWidth)
  })

  const browser: OpenPane = { icon: GlobeIcon, title: "thefork.pt", status: "", body: null }
  const view: OpenPane = { icon: FileTextIcon, title: "Trips", status: "", body: null }
  const split = (pane: OpenPane | undefined) => (
    <ThreadSplit thread={<p>The thread</p>} pane={pane} onClose={() => {}} />
  )
  /** The thread's share of the split, in percent. */
  const threadSize = () => Number(screen.getByRole("separator").getAttribute("aria-valuenow"))
  /** Widen the thread from the keyboard, as the handle's arrow keys do. */
  const widenThread = () => fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" })

  it("opens the pane at 44% thread and 56% pane", () => {
    render(split(browser))

    expect(threadSize()).toBe(44)
  })

  it("starts each open from 44%, however the last pane was resized", () => {
    const { rerender } = render(split(browser))
    widenThread()
    expect(threadSize()).toBe(49)

    rerender(split(undefined))
    rerender(split(view))

    expect(threadSize()).toBe(44)
  })

  it("keeps the split when a pane takes over from another without closing", () => {
    const { rerender } = render(split(browser))
    widenThread()

    rerender(split(view))

    expect(threadSize()).toBe(49)
  })
})
