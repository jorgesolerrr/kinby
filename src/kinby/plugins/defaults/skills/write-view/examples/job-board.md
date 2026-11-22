# A board a routine refreshes

A job search. Each morning a routine finds new postings and appends them to the data with
`view_patch`, and the user marks what they applied to by asking on a turn. The page never changes,
so the routine never asks.

The user comes to it to see what is new and what is next, so it opens with a sentence that says
so, not with a heading. The postings share their fields, so they sit in a table, at 0.875rem with
tight rows. The filters show their counts, the choice survives a redraw because it lives outside
`draw`, and the postings the user passed on fold away under a `<details>` that keeps its state for
the same reason. Colour marks only what is new and an interview. The board takes the full width
of the screen, so its table has room, and at pane width the salary and the date columns hide.

## Page

```html
<style>
  .board {
    container-type: inline-size;
  }
  .lede {
    margin: 0 0 1rem;
    color: var(--muted-foreground);
  }
  .lede strong {
    color: var(--foreground);
    font-weight: 600;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin-bottom: 0.5rem;
  }
  .filters button {
    border: 1px solid var(--border);
    border-radius: 999px;
    background: none;
    padding: 0.25rem 0.75rem;
    font-size: 0.875rem;
    cursor: pointer;
  }
  .filters button[aria-pressed="true"] {
    border-color: transparent;
    background: var(--secondary);
    font-weight: 500;
  }
  .count {
    margin-left: 0.375rem;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
  }
  table {
    font-size: 0.875rem;
  }
  th {
    font-size: 0.75rem;
  }
  th,
  td {
    padding: 0.5rem 0.75rem;
  }
  th:first-child,
  td:first-child {
    padding-left: 0;
  }
  th:last-child,
  td:last-child {
    padding-right: 0;
  }
  .role a {
    color: inherit;
    font-weight: 500;
    text-decoration: none;
  }
  .role a:hover {
    text-decoration: underline;
  }
  .where {
    display: block;
    color: var(--muted-foreground);
  }
  .role .new::before {
    content: "";
    display: inline-block;
    width: 0.4rem;
    height: 0.4rem;
    margin-right: 0.4rem;
    border-radius: 50%;
    background: var(--primary);
    vertical-align: 0.1em;
  }
  .number {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .status {
    color: var(--muted-foreground);
    white-space: nowrap;
  }
  .status.interview {
    color: var(--primary);
    font-weight: 500;
  }
  .note {
    display: block;
    font-weight: 400;
  }
  .empty {
    padding: 1.5rem 0;
    color: var(--muted-foreground);
  }
  details {
    margin-top: 1.5rem;
    font-size: 0.875rem;
  }
  summary {
    color: var(--muted-foreground);
    cursor: pointer;
  }
  details ul {
    margin: 0.5rem 0 0;
    padding: 0;
    list-style: none;
  }
  details li {
    padding: 0.25rem 0;
    color: var(--muted-foreground);
  }
  @container (max-width: 34rem) {
    .wide {
      display: none;
    }
  }
</style>

<body class="full-width">
<div class="board">
  <p class="lede" id="lede"></p>
  <div class="filters" id="filters"></div>
  <table>
    <thead>
      <tr>
        <th>Role</th>
        <th class="number wide">Salary</th>
        <th class="number wide">Found</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody id="postings"></tbody>
  </table>
  <details id="passed">
    <summary></summary>
    <ul></ul>
  </details>
</div>

<script>
  const statuses = { new: "New", applied: "Applied", interview: "Interview", passed: "Passed" }
  const filters = [
    { label: "Open", keeps: (posting) => posting.status !== "passed" },
    { label: "New", keeps: (posting) => posting.status === "new" },
    { label: "Applied", keeps: (posting) => posting.status === "applied" },
    { label: "Interview", keeps: (posting) => posting.status === "interview" },
  ]
  let chosen = filters[0]

  function element(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  function found(day) {
    const date = new Date(`${day}T00:00`)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const days = Math.round((today - date) / 86_400_000)
    if (days === 0) return "Today"
    if (days === 1) return "Yesterday"
    if (days < 7) return date.toLocaleDateString(undefined, { weekday: "short" })
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  }

  function row(posting) {
    const role = element("td", "role")
    const link = element("a", posting.status === "new" ? "new" : "", posting.role)
    link.href = posting.url
    role.append(link, element("span", "where", `${posting.company} · ${posting.location}`))
    const status = element("td", `status ${posting.status}`, statuses[posting.status])
    if (posting.note) status.append(element("span", "note", posting.note))
    const tr = element("tr")
    tr.append(
      role,
      element("td", "number wide", posting.salary ?? "—"),
      element("td", "number wide", found(posting.found)),
      status,
    )
    return tr
  }

  function draw(data) {
    const postings = data.postings.toSorted((a, b) => b.found.localeCompare(a.found))
    const counted = (status) => postings.filter((posting) => posting.status === status).length

    const lede = document.getElementById("lede")
    lede.replaceChildren(
      element("strong", "", `${counted("new")} new`),
      " since the last check, ",
      element("strong", "", `${counted("applied")} applied`),
      " and waiting, ",
      element("strong", "", `${counted("interview")} interview`),
      " coming up.",
    )

    const buttons = filters.map((filter) => {
      const button = element("button", "", filter.label)
      button.type = "button"
      button.setAttribute("aria-pressed", filter === chosen)
      button.append(element("span", "count", postings.filter(filter.keeps).length))
      button.addEventListener("click", () => {
        chosen = filter
        draw(kinby.data)
      })
      return button
    })
    document.getElementById("filters").replaceChildren(...buttons)

    const shown = postings.filter(chosen.keeps)
    const rows = shown.map(row)
    if (rows.length === 0) {
      const empty = element("td", "empty", "Nothing here right now.")
      empty.colSpan = 4
      rows.push(element("tr"))
      rows[0].append(empty)
    }
    document.getElementById("postings").replaceChildren(...rows)

    const passed = postings.filter((posting) => posting.status === "passed")
    const fold = document.getElementById("passed")
    fold.hidden = passed.length === 0
    fold.querySelector("summary").textContent = `Passed on ${passed.length}`
    fold.querySelector("ul").replaceChildren(
      ...passed.map((posting) =>
        element("li", "", `${posting.role}, ${posting.company}${posting.note ? `: ${posting.note}` : ""}`),
      ),
    )
  }

  draw(kinby.data)
  kinby.onData(draw)
</script>
```

## Data

```json
{
  "postings": [
    {
      "role": "Senior Backend Engineer",
      "company": "Northwind",
      "location": "Remote, EU",
      "salary": "€85–100k",
      "found": "2026-11-21",
      "status": "new",
      "url": "https://jobs.example/northwind/senior-backend"
    },
    {
      "role": "Staff Engineer, Payments",
      "company": "Globex",
      "location": "Lisbon or remote",
      "salary": "€110–130k",
      "found": "2026-11-21",
      "status": "new",
      "url": "https://jobs.example/globex/staff-payments"
    },
    {
      "role": "Platform Engineer",
      "company": "Umbrella Health",
      "location": "Madrid",
      "found": "2026-11-20",
      "status": "new",
      "url": "https://jobs.example/umbrella/platform"
    },
    {
      "role": "Backend Engineer, Search",
      "company": "Initech",
      "location": "Remote, Europe",
      "salary": "€80–95k",
      "found": "2026-11-12",
      "status": "interview",
      "note": "Thu 26 Nov, 10:00",
      "url": "https://jobs.example/initech/search"
    },
    {
      "role": "Senior Python Engineer",
      "company": "Hooli",
      "location": "Berlin",
      "salary": "€90–105k",
      "found": "2026-11-17",
      "status": "applied",
      "url": "https://jobs.example/hooli/python"
    },
    {
      "role": "Software Engineer, Data",
      "company": "Stark Logistics",
      "location": "Remote, EU",
      "salary": "€75–90k",
      "found": "2026-11-14",
      "status": "applied",
      "note": "Referred by Ana",
      "url": "https://jobs.example/stark/data"
    },
    {
      "role": "Engineering Manager",
      "company": "Acme",
      "location": "Barcelona",
      "salary": "€100–120k",
      "found": "2026-11-09",
      "status": "applied",
      "url": "https://jobs.example/acme/em"
    },
    {
      "role": "Senior Engineer, Infra",
      "company": "Soylent",
      "location": "Amsterdam",
      "salary": "€95–110k",
      "found": "2026-11-06",
      "status": "passed",
      "note": "on-site five days",
      "url": "https://jobs.example/soylent/infra"
    },
    {
      "role": "Backend Developer",
      "company": "Vandelay",
      "location": "Remote",
      "salary": "€60–70k",
      "found": "2026-11-05",
      "status": "passed",
      "note": "below range",
      "url": "https://jobs.example/vandelay/backend"
    }
  ]
}
```
