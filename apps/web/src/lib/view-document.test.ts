import { afterEach, describe, expect, it, vi } from "vitest"

import { viewDocument } from "@/lib/view-document"

const PAGE = `<ul id="postings"></ul>
<script>
  window.heard = []
  kinby.onData((data) => window.heard.push(data))
</script>`

/**
 * The page's window, with the document drawn for `data` loaded in a frame of this one. What the
 * page posts to the app is caught on its parent's postMessage.
 */
function load(
  data: Record<string, unknown>,
  dark = false,
  drawn = PAGE,
): Window & { heard?: unknown[] } {
  const frame = document.createElement("iframe")
  document.body.append(frame)
  const page = frame.contentWindow as Window & typeof globalThis
  page.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.spyOn(page.parent, "postMessage").mockImplementation(() => {})
  page.document.open()
  page.document.write(viewDocument(drawn, data, { dark, origin: window.location.origin }))
  page.document.close()
  return page
}

/** Post `message` into `page` as `source` would. */
function post(page: Window, message: unknown, source: Window = page.parent) {
  const { MessageEvent } = page as Window & typeof globalThis
  page.dispatchEvent(new MessageEvent("message", { data: message, source }))
}

function kinby(page: Window): { data: unknown } {
  return (page as Window & { kinby: { data: unknown } }).kinby
}

describe("the bridge in a view's page", () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  it("hands the page its data, and calls kinby.onData with each change the app posts", () => {
    const page = load({ postings: ["Acme"] })
    expect(kinby(page).data).toEqual({ postings: ["Acme"] })
    expect(page.heard).toEqual([])

    post(page, { kinby: "data", data: { postings: ["Acme", "Globex"] } })
    post(page, { kinby: "data", data: { postings: ["Globex"] } })

    expect(page.heard).toEqual([{ postings: ["Acme", "Globex"] }, { postings: ["Globex"] }])
    expect(kinby(page).data).toEqual({ postings: ["Globex"] })
  })

  it("switches the page's theme when the app posts one", () => {
    const page = load({}, true)
    expect(page.document.documentElement.classList.contains("dark")).toBe(true)

    post(page, { kinby: "theme", dark: false })

    expect(page.document.documentElement.classList.contains("dark")).toBe(false)
  })

  it("hears only the app's window", () => {
    const page = load({ postings: [] })

    post(page, { kinby: "data", data: { postings: ["Fake"] } }, page)
    post(page, { kinby: "theme", dark: true }, page)

    expect(page.heard).toEqual([])
    expect(kinby(page).data).toEqual({ postings: [] })
    expect(page.document.documentElement.classList.contains("dark")).toBe(false)
  })

  it("posts each uncaught error and each policy violation in the page to the app", () => {
    // The page's own listener keeps jsdom from printing the error.
    const page = load(
      {},
      false,
      `<script>addEventListener("error", (event) => event.preventDefault())</script>
<script>kinby.data.posting.company</script>`,
    )

    const { Event } = page as Window & typeof globalThis
    const violation = Object.assign(new Event("securitypolicyviolation"), {
      blockedURI: "inline",
      effectiveDirective: "script-src-attr",
    })
    page.document.dispatchEvent(violation)

    // The spy load put on the parent.
    const posted = vi.spyOn(page.parent, "postMessage")
    expect(posted.mock.calls).toEqual([
      [{ kinby: "error", message: expect.stringContaining("reading 'company'") }, "*"],
      [{ kinby: "error", message: "The page's policy blocked inline (script-src-attr)" }, "*"],
    ])
  })
})
