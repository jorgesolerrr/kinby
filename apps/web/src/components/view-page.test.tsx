import type { InstanceClient, View } from "@kinby/contract"
import { CallError } from "@kinby/contract"
import { stubSubscriber } from "@kinby/contract/testing"
import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ViewPage } from "@/components/view-page"
import { drawnPage } from "@/components/view-testing"

const PAGE = `<h1>Applications</h1>
<ul id="postings"></ul>
<button id="sort" onclick="sort()">Sort</button>
<script>
  for (const posting of kinby.data.postings) document.getElementById("postings").append(posting.company)
</script>
<script src="https://cdn.example/chart.js"></script>`

const DATA: View["data"] = {
  postings: [{ company: "Acme" }, { company: "Globex" }],
}

function view(page: string | null = PAGE, data: View["data"] = DATA): View {
  return { id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z", page, data }
}

/** The view page following the view, once it shows `shown`. */
async function openPage(shown: View) {
  const subscriber = stubSubscriber()
  const page = render(<ViewPage client={subscriber} viewId="applications" />)
  const [subscription] = subscriber.subscriptions
  await act(async () => subscription.deliver(shown))
  // jsdom loads a frame a task after inserting it.
  await act(() => new Promise((resolve) => setTimeout(resolve)))
  return { subscription, page }
}

function frame(): HTMLIFrameElement {
  return screen.getByTitle("Job board") as HTMLIFrameElement
}

/** What the web app posts into the frame. */
function posts() {
  const posted = vi.spyOn(frame().contentWindow as Window, "postMessage")
  return () => posted.mock.calls.map(([message, origin]) => [message, origin])
}

/** The nonce the frame's policy lets scripts run with. */
function nonce(drawn: Document): string | undefined {
  const policy = drawn.querySelector("meta[http-equiv=Content-Security-Policy]")
  return policy?.getAttribute("content")?.match(/'nonce-([^']+)'/)?.[1]
}

describe("ViewPage", () => {
  afterEach(() => document.documentElement.classList.remove("dark"))

  it("shows when the view last changed", async () => {
    const { subscription } = await openPage(view())

    expect(await screen.findByText(/Changed Oct 25, 2026/)).toBeTruthy()
    expect([subscription.method, subscription.params]).toEqual([
      "view.subscribe",
      { id: "applications" },
    ])
  })

  it("draws the page in a frame that may run scripts and open popups but has no origin of its own", async () => {
    await openPage(view())

    const allowed = frame().getAttribute("sandbox")?.split(" ")
    expect(allowed).toContain("allow-scripts")
    expect(allowed).toContain("allow-popups")
    expect(allowed).not.toContain("allow-same-origin")
  })

  it("starts the frame's document with a policy that reaches only the hub's sandbox", async () => {
    await openPage(view())

    const drawn = drawnPage(frame())
    const policy = drawn.head.firstElementChild
    expect(policy?.getAttribute("http-equiv")).toBe("Content-Security-Policy")
    const sandbox = `${window.location.origin}/sandbox/`
    expect(policy?.getAttribute("content")?.split("; ")).toEqual([
      "default-src 'none'",
      `script-src 'nonce-${nonce(drawn)}' ${sandbox}`,
      `style-src 'unsafe-inline' ${sandbox}`,
      "img-src data:",
      "form-action 'none'",
      "base-uri 'none'",
    ])
  })

  it("stamps the nonce on every inline script the page was written with, and on no handler", async () => {
    await openPage(view())
    const first = drawnPage(frame())

    const inline = [...first.querySelectorAll<HTMLScriptElement>("script:not([src])")]
    expect(inline).toHaveLength(2)
    expect(inline.every((script) => script.nonce === nonce(first))).toBe(true)
    expect(first.getElementById("sort")?.hasAttribute("nonce")).toBe(false)
  })

  it("leaves a script loaded from an address without the nonce, so only the policy's path admits it", async () => {
    await openPage(view())

    const [remote] = drawnPage(frame()).querySelectorAll("script[src]")
    expect(remote.getAttribute("src")).toBe("https://cdn.example/chart.js")
    expect(remote.hasAttribute("nonce")).toBe(false)
  })

  it("reloads the frame with a new nonce when a snapshot brings a new page", async () => {
    const { subscription } = await openPage(view())
    const first = nonce(drawnPage(frame()))
    const posted = posts()

    await act(async () => subscription.deliver(view("<h1>Offers</h1>", { offers: [] })))

    const drawn = drawnPage(frame())
    expect(drawn.querySelector("h1")?.textContent).toBe("Offers")
    expect(drawn.querySelector("script")?.textContent).toContain('{"offers":[]}')
    expect(nonce(drawn)).not.toBe(first)
    expect(posted()).toEqual([])
  })

  it("draws the page again with the latest data when the page asks, and not when another window does", async () => {
    const { subscription } = await openPage(view())
    const postings = [{ company: "Initech" }]
    await act(async () => subscription.deliver(view(PAGE, { postings })))
    const drawn = frame().getAttribute("srcdoc")

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { kinby: "redraw" }, source: window }),
      )
    })
    expect(frame().getAttribute("srcdoc")).toBe(drawn)

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { kinby: "redraw" }, source: frame().contentWindow }),
      )
    })
    expect(frame().getAttribute("srcdoc")).not.toBe(drawn)
    expect(drawnPage(frame()).querySelector("script")?.textContent).toContain("Initech")
  })

  it("posts nothing into a new page until it loads, then posts what changed since", async () => {
    const { subscription } = await openPage(view())
    const posted = posts()
    await act(async () => subscription.deliver(view("<h1>Offers</h1>", { offers: [] })))

    await act(async () => subscription.deliver(view("<h1>Offers</h1>", { offers: ["Acme"] })))
    await act(async () => document.documentElement.classList.add("dark"))
    expect(posted()).toEqual([])

    fireEvent.load(frame())

    expect(posted()).toEqual([
      [{ kinby: "data", data: { offers: ["Acme"] } }, "*"],
      [{ kinby: "theme", dark: true }, "*"],
    ])
  })

  it("hands the page the view's data as kinby.data, where no value becomes script", async () => {
    const sneaky = { note: "</script><script>alert(1)</script>" }
    await openPage(view(PAGE, sneaky))

    const drawn = drawnPage(frame())
    const [bridge] = drawn.querySelectorAll("script")
    expect(drawn.querySelectorAll("script")).toHaveLength(3)
    expect(bridge.textContent).toContain("window.kinby")
    expect(bridge.textContent).toContain(
      String.raw`"\u003c/script>\u003cscript>alert(1)\u003c/script>"`,
    )
  })

  it("carries the theme's tokens for light and dark and the hub's base stylesheet", async () => {
    await openPage(view())

    const drawn = drawnPage(frame())
    const tokens = drawn.head.querySelector("style")?.textContent ?? ""
    expect(tokens).toMatch(/:root\s*\{[^}]*--background:/)
    expect(tokens).toMatch(/\.dark\s*\{[^}]*--background:/)
    const base = drawn.head.querySelector("link[rel=stylesheet]")
    expect(base?.getAttribute("href")).toBe(`${window.location.origin}/sandbox/base.css`)
    expect(drawn.documentElement.classList.contains("dark")).toBe(false)
  })

  it("draws the page dark when the app is dark", async () => {
    document.documentElement.classList.add("dark")

    await openPage(view())

    expect(drawnPage(frame()).documentElement.classList.contains("dark")).toBe(true)
  })

  it("posts the app's theme into the frame when it changes, and does not reload it", async () => {
    await openPage(view())
    const drawn = frame().getAttribute("srcdoc")
    const posted = posts()

    await act(async () => document.documentElement.classList.add("dark"))
    await act(async () => document.documentElement.classList.remove("dark"))

    expect(posted()).toEqual([
      [{ kinby: "theme", dark: true }, "*"],
      [{ kinby: "theme", dark: false }, "*"],
    ])
    expect(frame().getAttribute("srcdoc")).toBe(drawn)
  })

  it("posts the data of a snapshot with the same page into the frame, and does not reload it", async () => {
    const { subscription } = await openPage(view())
    const drawn = frame().getAttribute("srcdoc")
    const posted = posts()

    const postings = [...(DATA.postings as object[]), { company: "Initech" }]
    act(() =>
      subscription.deliver({ ...view(PAGE, { postings }), updated_at: "2026-10-26T08:00:00Z" }),
    )

    expect(await screen.findByText(/Changed Oct 26, 2026/)).toBeTruthy()
    expect(posted()).toEqual([[{ kinby: "data", data: { postings } }, "*"]])
    expect(frame().getAttribute("srcdoc")).toBe(drawn)
  })

  it("grows the frame to the height its page posts, and hears no other window", async () => {
    await openPage(view())

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { kinby: "height", height: 480 },
          source: frame().contentWindow,
        }),
      )
      window.dispatchEvent(
        new MessageEvent("message", { data: { kinby: "height", height: 9000 }, source: window }),
      )
    })

    expect(frame().style.getPropertyValue("--page-height")).toBe("480px")
  })

  it("says a view written for an older kinby can be written again, and draws nothing", async () => {
    await openPage(view(null))

    expect(await screen.findByText("Written for an older kinby")).toBeTruthy()
    expect(screen.getByText(/Ask the instance to write it again/)).toBeTruthy()
    expect(screen.queryByTitle("Job board")).toBeNull()
  })

  it("keeps the view it shows when the subscription fails, and says why", async () => {
    const { subscription } = await openPage(view())

    act(() =>
      subscription.fail(
        new CallError({ code: "INTERNAL", message: "The instance broke.", retryable: false }),
      ),
    )

    expect(await screen.findByText("The instance broke.")).toBeTruthy()
    expect(frame()).toBeTruthy()
  })

  it("says the view was deleted when its subscription ends, and drops what it showed", async () => {
    let end = () => {}
    const ended = new Promise<void>((resolve) => (end = resolve))
    async function* snapshots() {
      yield view()
      await ended
    }
    const subscribe = (() =>
      Object.assign(snapshots(), {
        head: new Promise<number>(() => {}),
        cancel() {},
      })) as InstanceClient["subscribe"]
    render(<ViewPage client={{ subscribe }} viewId="applications" />)
    expect(await screen.findByTitle("Job board")).toBeTruthy()

    act(() => end())

    expect(await screen.findByText("This view was deleted")).toBeTruthy()
    expect(screen.queryByText(/Changed/)).toBeNull()
    expect(screen.queryByTitle("Job board")).toBeNull()
  })

  it("says the view was deleted when the instance does not find it", async () => {
    const subscriber = stubSubscriber()
    render(<ViewPage client={subscriber} viewId="applications" />)

    act(() =>
      subscriber.subscriptions[0].fail(
        new CallError({
          code: "NOT_FOUND",
          message: 'View "applications" was not found.',
          retryable: false,
        }),
      ),
    )

    expect(await screen.findByText("This view was deleted")).toBeTruthy()
    expect(screen.queryByText("The instance refused it")).toBeNull()
  })

  it("stops following the view when the page closes", async () => {
    const { subscription, page } = await openPage(view())

    page.unmount()

    expect(subscription.cancelled).toBe(true)
  })
})
