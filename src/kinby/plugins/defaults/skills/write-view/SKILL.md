---
name: write-view
description: Write a view's page and data. Use when creating or reshaping a view.
---

A view is a page the user opens in the web app. Its `page` is HTML you write, and its `data` is a
JSON object the page shows. Call `view_write` with the view's `id`, `title`, `page` and `data` to
create or replace one. The whole view is capped at 1 MB.

On a turn the user starts, `view_write` runs without asking. On any other turn, such as a
routine's, it asks the user first when it creates a view or changes a page, because a page is
code. Writing a view whole with the page it already has changes only the data, and never asks.

## The page and its data

Keep the layout in the page and the content in the data. Then a routine that refreshes the view
changes only the data, with `view_patch`, and never asks. A view you make once, such as a trip
plan, may keep everything in its page.

The page reads the view's data as `kinby.data` when it loads. Draw it with a script:

```html
<h1>Applications</h1>
<ul id="postings"></ul>
<script>
  const list = document.getElementById("postings")
  for (const posting of kinby.data.postings) {
    const item = document.createElement("li")
    item.textContent = posting.company
    list.append(item)
  }
</script>
```

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

To change the title or the data, call `view_read`, then `view_patch` with JSON Patch operations
instead of writing the view again. Paths address the view as `view_read` returns it:
`/data/postings/-` appends a posting, and `/title` renames it. A patch can't touch the page: write
the view again with `view_write` to change it.

A view written for an older kinby has no page. Read its data with `view_read` and write it again
with a page.

## Example

A board of job applications, by stage.

```json
{
  "id": "applications",
  "title": "Applications",
  "page": "<style>.stages{display:flex;gap:1rem}.stage{flex:1;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:.75rem}</style>\n<div class=\"stages\" id=\"stages\"></div>\n<script>\n  for (const stage of kinby.data.stages) {\n    const column = document.createElement(\"section\")\n    column.className = \"stage\"\n    const title = document.createElement(\"h2\")\n    title.textContent = stage.name\n    column.append(title)\n    for (const application of stage.applications) {\n      const link = document.createElement(\"a\")\n      link.href = application.url\n      link.textContent = application.company\n      column.append(link, document.createElement(\"br\"))\n    }\n    document.getElementById(\"stages\").append(column)\n  }\n</script>",
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
