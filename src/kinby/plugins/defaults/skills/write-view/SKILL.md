---
name: write-view
description: Compose a view's spec and data from the view catalogue. Use when creating or reshaping a view.
---

A view is a page of UI the user opens in the web app. Its `spec` lays it out from the view
catalogue, and its `data` holds what it shows. Call `view_write` with the view's `id`, `title`,
`spec` and `data` to create or replace one. A view is capped at 1 MB.

## The spec

The spec is `{"components": [...]}`, a flat list of components. Each one has a unique `id` and a
`component` naming what it is in the catalogue. The component with the id `root` is the one
rendered. A component names its children, its template or its table cells by id. Every id it
names must exist, and no component may contain itself.

Keep the layout in the spec and the content in the data. Then a routine that refreshes the view
changes only the data.

## Props

A prop is a literal, or a binding `{"path": "<JSON Pointer>"}` that reads the data, such as
`{"path": "/stages/0/name"}`. The pointer `""` reads the whole data, or the whole item inside a
list. A text prop is a string, or a binding to a string, number or boolean. A binding that names
nothing renders as empty.

## Lists

`Card` and `Row` children may be a list instead of ids:
`{"path": "/postings", "template": "posting"}` renders the `posting` component once per item of
the array at `/postings`. Inside it, the template and the components it names read their paths in
the item, so `{"path": "/company"}` is the item's company.

The repeating components work the same way. Each reads an array, and reads some props in each
item:

| Component | Array | Read in each item |
|---|---|---|
| `Table` | `rows` | each column's `cell` |
| `Kanban` | `columns` | `title` and `cards` |
| `Chart` | `points` | `x` and each series' `value` |
| `Checklist` | `items` | `label` and `done` |

## The view catalogue

Optional props are marked with ?.

| Component | Props | Shows |
|---|---|---|
| `Card` | `title`?, `description`?, `children`? | A card that stacks its children. |
| `Row` | `children`? | Its children side by side, wrapping when they run out of room, such as a few Stats. |
| `Text` | `text` | A paragraph. |
| `Link` | `label`, `href` | A link opening an http, https or mailto address in a new tab. |
| `Badge` | `label` | A short label, such as a stage. |
| `Stat` | `label`, `value`, `description`? | One figure with its label, and a line under it. |
| `Table` | `rows`, `columns` | One row per item. A column is `{"header": <text>, "cell": "<component id>"}`. |
| `Kanban` | `columns`, `title`, `cards` | One column per item, titled `title`. `cards` is a list `{"path", "template"}` read in the column. |
| `Chart` | `kind`, `points`, `x`, `series` | A `line`, `bar` or `area` chart. A series is `{"label": "<text>", "value": <binding to a number>}`; give at least one. |
| `Checklist` | `items`, `label`, `done` | A read-only checklist. `done` is a boolean, or a binding that is checked when it reads `true`. |

`rows`, `points`, `x`, `items` and a `Kanban`'s `columns` are always bindings.

## Changing a view

To change part of a view, call `view_read`, then `view_patch` with JSON Patch operations instead
of writing it again. Paths address the view as `view_read` returns it: `/data/postings/-` appends
a posting, `/spec/components/2/columns/-` adds a column to the third component, and `/title`
renames it. The patched view must still be valid, or nothing is written.

## Example

A board of job applications: two figures over a kanban of stages.

```json
{
  "id": "applications",
  "title": "Applications",
  "spec": {
    "components": [
      {"id": "root", "component": "Card", "title": "Job search", "children": ["numbers", "board"]},
      {"id": "numbers", "component": "Row", "children": ["open", "sent"]},
      {"id": "open", "component": "Stat", "label": "Open", "value": {"path": "/open"}},
      {"id": "sent", "component": "Stat", "label": "Sent this week", "value": {"path": "/sent"}},
      {
        "id": "board",
        "component": "Kanban",
        "columns": {"path": "/stages"},
        "title": {"path": "/name"},
        "cards": {"path": "/applications", "template": "application"}
      },
      {
        "id": "application",
        "component": "Card",
        "title": {"path": "/company"},
        "description": {"path": "/role"},
        "children": ["posting"]
      },
      {"id": "posting", "component": "Link", "label": "Posting", "href": {"path": "/url"}}
    ]
  },
  "data": {
    "open": 3,
    "sent": 5,
    "stages": [
      {
        "name": "Applied",
        "applications": [
          {"company": "Acme", "role": "Engineer", "url": "https://jobs.example/acme"},
          {"company": "Globex", "role": "Designer", "url": "https://jobs.example/globex"}
        ]
      },
      {
        "name": "Interview",
        "applications": [
          {"company": "Initech", "role": "Engineer", "url": "https://jobs.example/initech"}
        ]
      }
    ]
  }
}
```
