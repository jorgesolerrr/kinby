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

Keep the layout in the page and the content in the data. Then a routine that refreshes the view
changes only the data, with `view_patch`, and never asks. A view you make once, such as a trip
plan, may keep everything in its page.

The page reads the view's data as `kinby.data`. While the user has the view open, a change to the
data doesn't reload the page: `kinby.onData(callback)` calls `callback` with the new data, and
`kinby.data` holds it. Draw from a function, call it with `kinby.data`, and hand it to
`kinby.onData`:

```html
<h1>Applications</h1>
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
tables. Style the rest with a `<style>` element or `style` attributes.

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

## Example

A board of job applications, by stage.

```json
{
  "id": "applications",
  "title": "Applications",
  "page": "<style>.stages{display:flex;gap:1rem}.stage{flex:1;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:.75rem}</style>\n<div class=\"stages\" id=\"stages\"></div>\n<script>\n  function draw(data) {\n    const columns = data.stages.map((stage) => {\n      const column = document.createElement(\"section\")\n      column.className = \"stage\"\n      const title = document.createElement(\"h2\")\n      title.textContent = stage.name\n      column.append(title)\n      for (const application of stage.applications) {\n        const link = document.createElement(\"a\")\n        link.href = application.url\n        link.textContent = application.company\n        column.append(link, document.createElement(\"br\"))\n      }\n      return column\n    })\n    document.getElementById(\"stages\").replaceChildren(...columns)\n  }\n  draw(kinby.data)\n  kinby.onData(draw)\n</script>",
  "data": {
    "stages": [
      {
        "name": "Applied",
        "applications": [
          {"company": "Acme", "url": "https://jobs.example/acme"},
          {"company": "Globex", "url": "https://jobs.example/globex"}
        ]
      },
      {
        "name": "Interview",
        "applications": [{"company": "Initech", "url": "https://jobs.example/initech"}]
      }
    ]
  }
}
```
