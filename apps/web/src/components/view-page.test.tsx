import type { View, ViewComponent } from "@kinby/contract"
import { CallError } from "@kinby/contract"
import { type Answers, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

function openPage(shown: View, answers: Answers = {}) {
  const subscriber = stubSubscriber()
  const caller = stubCaller(answers)
  const page = render(
    <ViewPage
      client={{ subscribe: subscriber.subscribe, call: caller.call }}
      instanceId="hub-ada"
      viewId="applications"
    />,
  )
  const [subscription] = subscriber.subscriptions
  act(() => subscription.deliver(shown))
  return { subscription, page, caller }
}

async function askToDelete() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "More actions" }))
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }))
  return { user, dialog: await screen.findByRole("alertdialog") }
}

describe("ViewPage", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows the view's title and when it last changed", async () => {
    const { subscription } = openPage(view())

    expect(await screen.findByRole("heading", { name: "Job board" })).toBeTruthy()
    expect(screen.getByText(/Changed Oct 25, 2026/)).toBeTruthy()
    expect([subscription.method, subscription.params]).toEqual([
      "view.subscribe",
      { id: "applications" },
    ])
  })

  it("shows each new snapshot of the view as it arrives", async () => {
    const { subscription } = openPage(view())
    expect(await screen.findByText("Globex")).toBeTruthy()

    const postings = [...(DATA.postings as object[]), { company: "Initech", stage: "Applied" }]
    act(() =>
      subscription.deliver({
        ...view(BOARD, { ...DATA, postings }),
        updated_at: "2026-10-26T08:00:00Z",
      }),
    )

    expect(await screen.findByText("Initech")).toBeTruthy()
    expect(screen.getByText(/Changed Oct 26, 2026/)).toBeTruthy()
  })

  it("keeps the view it shows when the subscription fails, and says why", async () => {
    const { subscription } = openPage(view())
    expect(await screen.findByText("Globex")).toBeTruthy()

    act(() =>
      subscription.fail(
        new CallError({ code: "INTERNAL", message: "The instance broke.", retryable: false }),
      ),
    )

    expect(await screen.findByText("The instance broke.")).toBeTruthy()
    expect(screen.getByText("Globex")).toBeTruthy()
  })

  it("stops following the view when the page closes", async () => {
    const { subscription, page } = openPage(view())
    await screen.findByText("Globex")

    page.unmount()

    expect(subscription.cancelled).toBe(true)
  })

  it("deletes the view from its ⋯ menu once confirmed, and goes back to the instance", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/views/applications")
    const { caller } = openPage(view(), { "view.delete": () => ({ views: [] }) })

    const { user, dialog } = await askToDelete()
    expect(within(dialog).getByRole("heading", { name: "Delete Job board?" })).toBeTruthy()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada"))
    expect(caller.calls).toEqual([{ method: "view.delete", params: { id: "applications" } }])
  })

  it("deletes nothing when the confirm is cancelled", async () => {
    const { caller } = openPage(view(), { "view.delete": () => ({ views: [] }) })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(caller.calls).toEqual([])
  })

  it("says why a delete failed in the confirm, and keeps the view", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/views/applications")
    openPage(view(), {
      "view.delete": () => {
        throw new CallError({ code: "INTERNAL", message: "The disk is full.", retryable: false })
      },
    })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    expect(await within(dialog).findByText("The disk is full.")).toBeTruthy()
    expect(screen.getByText("Globex")).toBeTruthy()
    expect(window.location.pathname).toBe("/instances/hub-ada/views/applications")
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

/** A view with every component of the catalogue: a job board as a kanban, a table and more. */
const CATALOGUE: ViewComponent[] = [
  {
    id: "root",
    component: "Card",
    title: "Job search",
    children: ["intro", "open", "board", "postings", "trend", "todo"],
  },
  { id: "intro", component: "Text", text: "Where every application stands." },
  {
    id: "open",
    component: "Stat",
    label: "Open",
    value: { path: "/open" },
    description: "+1 today",
  },
  {
    id: "board",
    component: "Kanban",
    columns: { path: "/stages" },
    title: { path: "/name" },
    cards: { path: "/applications", template: "application" },
  },
  { id: "application", component: "Card", title: { path: "/company" } },
  {
    id: "postings",
    component: "Table",
    rows: { path: "/postings" },
    columns: [
      { header: "Company", cell: "company" },
      { header: "Stage", cell: "stage" },
      { header: "Posting", cell: "posting" },
    ],
  },
  { id: "company", component: "Text", text: { path: "/company" } },
  { id: "stage", component: "Badge", label: { path: "/stage" } },
  { id: "posting", component: "Link", label: "Open", href: { path: "/url" } },
  {
    id: "trend",
    component: "Chart",
    kind: "line",
    points: { path: "/weeks" },
    x: { path: "/week" },
    series: [{ label: "Applications sent", value: { path: "/sent" } }],
  },
  {
    id: "todo",
    component: "Checklist",
    items: { path: "/todo" },
    label: { path: "/task" },
    done: { path: "/done" },
  },
]

const SEARCH: View["data"] = {
  open: 3,
  stages: [
    { name: "Applied", applications: [{ company: "Acme" }, { company: "Globex" }] },
    { name: "Interview", applications: [{ company: "Initech" }] },
  ],
  postings: [
    { company: "Acme", stage: "Applied", url: "https://jobs.example/acme" },
    { company: "Initech", stage: "Interview", url: "https://jobs.example/initech" },
  ],
  weeks: [
    { week: "W1", sent: 2 },
    { week: "W2", sent: 5 },
  ],
  todo: [
    { task: "Follow up with Acme", done: true },
    { task: "Update the CV", done: false },
  ],
}

describe("ViewPage, with every catalogue component", () => {
  it("renders each component with the data it binds", async () => {
    openPage(view(CATALOGUE, SEARCH))

    expect(await screen.findByText("Where every application stands.")).toBeTruthy()
    const stat = screen.getByRole("region", { name: "Open" })
    expect(within(stat).getByText("3")).toBeTruthy()
    expect(within(stat).getByText("+1 today")).toBeTruthy()
    expect(screen.getByRole("region", { name: "Applied" })).toBeTruthy()
    expect(within(screen.getByRole("table")).getByText("Interview")).toBeTruthy()
    expect(screen.getByText("Applications sent")).toBeTruthy()
    expect(document.querySelector(".recharts-line")).toBeTruthy()
    const done = screen.getByRole("checkbox", { name: "Follow up with Acme" })
    expect(done.getAttribute("aria-checked")).toBe("true")
    const open = screen.getByRole("checkbox", { name: "Update the CV" })
    expect(open.getAttribute("aria-checked")).toBe("false")
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("shows a kanban column per entry in the data, with a card per item", async () => {
    openPage(view(CATALOGUE, SEARCH))

    const applied = await screen.findByRole("region", { name: "Applied" })
    const interview = screen.getByRole("region", { name: "Interview" })
    expect(within(applied).getByText("Acme")).toBeTruthy()
    expect(within(applied).getByText("Globex")).toBeTruthy()
    expect(within(interview).getByText("Initech")).toBeTruthy()
    expect(within(interview).queryByText("Acme")).toBeNull()
  })

  it("shows a table row per item, each cell read in its row", async () => {
    openPage(view(CATALOGUE, SEARCH))

    const table = await screen.findByRole("table")
    const [header, ...rows] = within(table).getAllByRole("row")
    expect(
      within(header)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual(["Company", "Stage", "Posting"])
    expect(
      rows.map((row) =>
        within(row)
          .getAllByRole("cell")
          .map((cell) => cell.textContent),
      ),
    ).toEqual([
      ["Acme", "Applied", "Open"],
      ["Initech", "Interview", "Open"],
    ])
    expect(within(rows[1]).getByRole("link").getAttribute("href")).toBe(
      "https://jobs.example/initech",
    )
  })

  it.each([
    ["bar", ".recharts-bar"],
    ["area", ".recharts-area"],
  ] as const)("draws a %s chart", async (kind, drawn) => {
    const chart = { ...CATALOGUE[9], kind } as ViewComponent
    openPage(view([...CATALOGUE.slice(0, 9), chart, CATALOGUE[10]], SEARCH))

    expect(await screen.findByText("Applications sent")).toBeTruthy()
    expect(document.querySelector(drawn)).toBeTruthy()
  })
})
