import type { View, ViewComponent } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ViewPage } from "@/components/view-page"

const BOARD: ViewComponent[] = [
  { id: "root", component: "Card", title: { path: "/heading" }, children: ["intro", "postings"] },
  { id: "intro", component: "Text", text: "Open applications, by company." },
  { id: "postings", component: "Card", children: { path: "/postings", template: "posting" } },
  {
    id: "posting",
    component: "Card",
    title: { path: "/company" },
    description: { path: "/stage" },
    children: ["link"],
  },
  { id: "link", component: "Link", label: "Posting", href: { path: "/url" } },
]

const DATA: View["data"] = {
  heading: "Applications",
  postings: [
    { company: "Acme", stage: "Interview", url: "https://jobs.example/acme" },
    { company: "Globex", stage: "Applied", url: "javascript:alert(1)" },
  ],
}

function view(components: ViewComponent[] = BOARD, data: View["data"] = DATA): View {
  return {
    id: "applications",
    title: "Job board",
    updated_at: "2026-10-25T09:30:00Z",
    spec: { components },
    data,
  }
}

function openPage(shown: View) {
  const caller = stubCaller({ "view.get": () => shown })
  render(<ViewPage client={caller} clock={fakeClock()} viewId="applications" />)
  return caller
}

describe("ViewPage", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows the view's title and when it last changed", async () => {
    const caller = openPage(view())

    expect(await screen.findByRole("heading", { name: "Job board" })).toBeTruthy()
    expect(screen.getByText(/Changed Oct 25, 2026/)).toBeTruthy()
    expect(caller.calls).toEqual([{ method: "view.get", params: { id: "applications" } }])
  })

  it("renders its components with the data they bind", async () => {
    openPage(view())

    expect(await screen.findByText("Applications")).toBeTruthy()
    expect(screen.getByText("Open applications, by company.")).toBeTruthy()
    expect(screen.getByText("Interview")).toBeTruthy()
  })

  it("repeats a list template once per item, reading its paths in the item", async () => {
    openPage(view())

    expect(await screen.findByText("Acme")).toBeTruthy()
    expect(screen.getByText("Globex")).toBeTruthy()
    expect(screen.getAllByText("Posting")).toHaveLength(2)
  })

  it("opens a link's target in a new tab, and shows one it cannot open as text", async () => {
    openPage(view())

    const [link] = await screen.findAllByRole("link", { name: "Posting" })
    expect(link.getAttribute("href")).toBe("https://jobs.example/acme")
    expect(link.getAttribute("target")).toBe("_blank")
    expect(screen.getAllByRole("link")).toHaveLength(1)
  })

  it("shows an unresolved path as empty", async () => {
    openPage(view(BOARD, { postings: [] }))

    expect(await screen.findByText("Open applications, by company.")).toBeTruthy()
    expect(screen.queryByText("Applications")).toBeNull()
  })

  it("shows an unknown component as an inline error and the rest of the view", async () => {
    const marquee = { id: "intro", component: "Marquee" } as unknown as ViewComponent
    openPage(view([BOARD[0], marquee, ...BOARD.slice(2)]))

    const error = await screen.findByRole("alert")
    expect(within(error).getByText(/"Marquee"/)).toBeTruthy()
    expect(screen.getByText("Applications")).toBeTruthy()
    expect(screen.getByText("Acme")).toBeTruthy()
  })

  it("shows a component that fails to render as an inline error and the rest of the view", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    const broken = { id: "intro", component: "Card", children: 5 } as unknown as ViewComponent
    openPage(view([BOARD[0], broken, ...BOARD.slice(2)]))

    expect(await screen.findByRole("alert")).toBeTruthy()
    expect(screen.getByText("Applications")).toBeTruthy()
    expect(screen.getByText("Acme")).toBeTruthy()
  })
})
