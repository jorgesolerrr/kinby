---
name: write-view
description: Write a view's page and data. Use when creating or reshaping a view.
---

A view is a page the user opens in the web app. Its `page` is HTML you write, and its `data` is a
JSON object the page shows. Call `view_write` with the view's `id`, `title`, `page` and `data` to
create or replace one. The whole view is capped at 1 MB.

On a turn the user starts, `view_write` and `view_edit` run without asking. On any other turn,
such as a routine's, creating a view or changing its page asks the user first, because a page is
code. Writing a view whole with the page it already has changes only the data, and never asks.

## The page and its data

Keep the layout in the page and the content in the data whenever something will change the content
after this turn, such as a routine that refreshes it or a later turn that adds to it. Then the
change touches only the data, with `view_patch`, and never asks. A view you make once that nothing
refreshes, such as a trip plan, may keep everything in its page, with `{}` as its data. When
unsure, put the content in the data: moving it there later means writing the page again.

The page reads the view's data as `kinby.data`. While the user has the view open, a change to the
data doesn't reload the page: `kinby.onData(callback)` calls `callback` with the new data, and
`kinby.data` holds it. Draw from a function, call it with `kinby.data`, and hand it to
`kinby.onData`:

```html
<ul id="postings"></ul>
<script>
  function draw(data) {
    const items = data.postings.map((posting) => {
      const item = document.createElement("li")
      item.textContent = posting.company
      return item
    })
    document.getElementById("postings").replaceChildren(...items)
  }
  draw(kinby.data)
  kinby.onData(draw)
</script>
```

A redraw keeps the user's scroll position. Keep what the user chose, such as a filter or a sort, in
a variable outside `draw`, and apply it there, so the redraw keeps it too. A page that never calls
`kinby.onData` loads again with each change, and the user loses their place.

Put text from the data in with `textContent`, never `innerHTML`, so a value can't become markup.

## What runs

The page runs in a sandbox. It reaches nothing over the network: no `fetch`, no remote images, no
scripts or stylesheets from a CDN. Images go in the page as `data:` addresses. Forms don't submit.

Only the `<script>` elements you write in the page run. A handler written as an attribute, such as
`onclick="..."`, never runs, and neither does script you add later. Attach every handler from
script:

```js
document.getElementById("sort").addEventListener("click", () => sortByDate())
```

A link to an `http`, `https` or `mailto` address opens in a new tab.

## The theme

The page has kinby's theme as CSS variables, and they follow the app's light or dark theme. Use
them, not your own colours:

| Variable | For |
|---|---|
| `--background`, `--foreground` | The page and its text. |
| `--card`, `--card-foreground` | A raised surface and its text. |
| `--muted`, `--muted-foreground` | A quiet surface, and secondary text. |
| `--primary`, `--primary-foreground` | kinby's green, and text on it. |
| `--secondary`, `--accent`, `--accent-foreground` | Soft fills, such as a selected row. |
| `--border` | Lines and dividers. |
| `--destructive`, `--success` | Bad and good news. |
| `--chart-1` to `--chart-5` | Series in a drawing, in this order. |
| `--radius` | The corner of a card, 10px. |

A base stylesheet sets the page's background, text and spacing, and styles headings, links and
tables. Style the rest with a `<style>` element or `style` attributes. No font loads in the
sandbox, so name none: the system font the base stylesheet sets is the page's font.

## Charts

The hub serves Chart.js 4.6.1 to every page. Include it with a script tag before the script that
draws, and it defines `Chart`:

```html
<script src="/sandbox/chart.js"></script>
```

It is the only script library the hub serves. See https://www.chartjs.org/docs/4.6.1/ for its options.

Chart.js draws on a canvas, which can't read the theme's CSS variables, and it can't shade the
colours they hold. Turn each token into an `rgb()` colour with `token` below, and draw again when
the theme switches, which changes the class of the page's root:

```html
<canvas id="spending" role="img" aria-label="Spending by month"></canvas>
<script src="/sandbox/chart.js"></script>
<script>
  const swatch = document.createElement("canvas").getContext("2d", { willReadFrequently: true })
  function token(name) {
    swatch.clearRect(0, 0, 1, 1)
    swatch.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name)
    swatch.fillRect(0, 0, 1, 1)
    const [red, green, blue] = swatch.getImageData(0, 0, 1, 1).data
    return `rgb(${red}, ${green}, ${blue})`
  }

  let chart
  function draw(data) {
    Chart.defaults.color = token("--muted-foreground")
    Chart.defaults.borderColor = token("--border")
    chart?.destroy()
    chart = new Chart(document.getElementById("spending"), {
      type: "line",
      data: {
        labels: data.months.map((month) => month.name),
        datasets: [
          {
            label: "Spent",
            data: data.months.map((month) => month.total),
            borderColor: token("--chart-1"),
            backgroundColor: token("--chart-1"),
          },
        ],
      },
    })
  }
  draw(kinby.data)
  kinby.onData(draw)
  new MutationObserver(() => draw(kinby.data)).observe(document.documentElement, {
    attributeFilter: ["class"],
  })
</script>
```

## Design

Design each view for what it shows and how the user uses it. A board the user checks each morning
is scanned in seconds, and a trip plan is read slowly, a few times. Decide what the user opens the
view for, and put that first.

The tab and the pane already show the view's title and when it last changed, so don't repeat
either in the page. Open with what matters: the figure, a sentence of status, or the list itself.

A view is drawn at the full width of the screen, and in a pane beside a thread as narrow as 350
pixels. Make it hold at both: let rows wrap, stack side-by-side blocks, and hide a table's
secondary columns at narrow widths with a container query.

### Type

Keep to four sizes:

| Size | For |
|---|---|
| 0.75rem | Column headers, labels and metadata. |
| 0.875rem | The rows of a table or a dense list. |
| 1rem | Prose, and the page's default. |
| 1.125rem | A section's heading, as the base stylesheet sets `h2`. |

The one figure a view exists to show may go to 2rem or 2.5rem. Show what matters with weight, 600
for headings and 500 for emphasis, and what is secondary with `--muted-foreground`, before you
reach for another size. Set figures in a column with `font-variant-numeric: tabular-nums` and
align them right. Keep prose under about 70 characters a line.

### Density

Match density to use. A list the user works daily is dense: 0.875rem rows with 0.5rem of padding,
and nothing spent on decoration. A summary or a plan the user reads can breathe. Space in steps
of 0.25rem, and group with space before you reach for a border: a gap between groups says more
than a box around each.

### Table, list, columns or cards

- A table when the items share their fields and the user compares across them: postings,
  transactions, papers. It is the densest layout and the quickest to scan, and the default past a
  handful of items.
- A list, one line to an item, when each item has one or two fields.
- Columns when the question is which stage each item is in, the stages are few and so are the
  items in each. Columns turn narrow fast.
- Cards only when the items differ in shape or each carries a long body, and there are few of them.

### Colour

Text and `--muted-foreground` carry the page. Colour means something: `--primary` for what is new
or what to act on, `--destructive` for what is late or over, `--success` for good news, and
`--chart-1` to `--chart-5` for series. Colour used for decoration drowns the colour that means
something.

### What makes a view look generic

- Cards inside cards, or every item in its own bordered box.
- A row of stat tiles of equal weight, when one figure matters most.
- Everything one size and weight, or everything centred.
- A coloured badge for every field.
- Emoji for icons, gradients and drop shadows.
- A heading over every block, when the content says what it is.
- Raw values: an ISO timestamp, `1234.5`, `null`, or an address as a link's text. Write "Tue 18
  Nov" and "€1,235", leave out what is empty or show a dash in its table cell, and link the name.

### Working a long list

Give a long list the controls the user would reach for: a filter that shows its counts, a sort,
and a fold for what is done. A `<details>` element folds with no script. Make each control a
`<button>`, mark the chosen one with `aria-pressed`, and keep the choice outside `draw`.

## Changing a view

Call `view_read` first, then change only what needs changing instead of writing the view again.

To change the title or the data, call `view_patch` with JSON Patch operations. Paths address the
view as `view_read` returns it: `/data/postings/-` appends a posting, and `/title` renames it. A
patch can't touch the page.

To change the page, call `view_edit` with `old`, an exact string from the page, and `new`, the
string to put in its place. It refuses when `old` is not in the page or appears more than once, so
quote enough of the page around the change that it appears once. Write the page whole with
`view_write` only to redesign it. A new page reloads the view for the user.

`view_read` also returns the `errors` the page last raised when the user opened it: uncaught
script errors and what the page's policy blocked. Fix the page when they show, and the next write
of the view clears them.

A view written for an older kinby has no page. Read its data with `view_read` and write it again
with a page.


## Examples

Three whole views of different kinds. Before you write a page, read the one nearest yours with
`skill` and its `file` argument, and match its level. Each holds the page and the data to pass to
`view_write`, and says why it is designed that way.

- `examples/job-board.md`: a board a routine refreshes. A table with filters and their counts,
  and a fold for what is done.
- `examples/spending-summary.md`: a summary with a chart. One figure, a chart of the months, and a
  table of categories.
- `examples/trip-plan.md`: a page made once. Everything is in the page, with no script and empty
  data.
