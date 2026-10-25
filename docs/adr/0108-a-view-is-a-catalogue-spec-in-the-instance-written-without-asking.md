# A view is a catalogue spec in the instance, written without asking

Ticket [#526](https://github.com/jorgesolerrr/kinby/issues/526): an instance should be able to show the user something more precise than a message, such as a board of job applications a daily routine keeps current. The research in [#519](https://github.com/jorgesolerrr/kinby/issues/519) found three tiers of generative UI (fixed components, a declared spec over a catalogue, sandboxed code) and no product that keeps a long-lived view a routine updates every morning. kinby gains the **view**.

## Decision

- **A view is a spec plus data, composed from the view catalogue.** The spec is a flat list of components with ids and a `root`, whose props are literals or JSON Pointer paths into the view's data, with list templates repeated per item. The shape follows Google A2UI, so adopting A2UI later is a mapping. No view carries code: the browser renders only components kinby ships.
- **The view catalogue lives in the contracts.** Each component (Card, Table, Badge, Kanban, Stat, Chart, Checklist, Link, Text) is a model in a discriminated union. The tools' validation and the web app's types come from that one source through the exported schema. The catalogue grows only with a kinby release.
- **The agent owns its views.** Any turn or routine may create a view on its own initiative, and may change its spec or its data. Nothing restricts a routine to the data.
- **A view is a file in the instance, outside the workspace.** `views/<id>.json` holds its title, spec and data, beside `routines/` and `skills/`. Workspace file tools never reach it, so every write goes through the view tools and their validation. A turn that changes a view records a `view.changed` event in its thread, with the view's id and title but not its content.
- **View writes are allowed like `notify`.** `view_write` and `view_patch` are allowed in every mode but read-only, under their own rule that `permissions.toml` can tighten, so a routine never parks to update a board. `view_delete` asks under the ordinary gate, since a deleted view cannot come back.
- **The browser gets whole snapshots.** `view.subscribe` sends the whole view, then the whole view again after each change. A view is capped at 1 MB, so a snapshot is cheap and the browser's copy cannot drift.
- **A view is read-only to the user in v1.** It shows data and links. The user changes it by asking the agent, and can delete it from the web app.

## Considered options

- **Fixed view kinds (board, table, list, checklist).** Rejected. It's cheapest, but "add a salary column" works only if the kind already has it, and each new kind needs a web app release.
- **Sandboxed HTML written by the agent (MCP Apps style).** Rejected for now. It needs a second origin, a CSP and theme bridging, it won't look like the rest of the app, and a routine that reads job postings or email would be writing code that runs in the user's browser. It stays open as a later catalogue component that hosts one.
- **Data the agent writes, spec only on turns the user starts.** Rejected. The user wants the agent to own its views outright. With a catalogue, a prompt-injected routine can at worst rearrange kinby's own components.
- **Views in the event log.** Rejected. The event log is per thread, and a view outlives every thread.
- **Views under the workspace.** Rejected. Workspace revert would come for free, but the shell would write views past validation.
- **Snapshot plus JSON Patch deltas over the socket.** Rejected for now. Views are small, and whole snapshots need no ordering or resync logic.

## Consequences

- An instance gains a `views/` directory, the view tools as core tools, and `view.*` contract methods. The gate gains a view rule next to `notify`'s.
- A view has no history or undo, so a bad write by the agent is fixed by asking the agent.
- Direct edits by the user, actions that start a turn, views shipped by a library or factory, the home instance writing other instances' views, and a sandboxed escape hatch are deferred.
