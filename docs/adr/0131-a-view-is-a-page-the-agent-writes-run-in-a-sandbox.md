# A view is a page the agent writes, run in a sandbox

Supersedes [ADR 0108](0108-a-view-is-a-catalogue-spec-in-the-instance-written-without-asking.md).

ADR 0108 composed every view from a catalogue of ten components, so that no view carried code. The views came out alike: cards in cards, a table, a kanban. The user wants views the model designs. Of the generative UI products the research in [#519](https://github.com/jorgesolerrr/kinby/issues/519) covered, MCP Apps fits a view that lives for months: a page written once, with the host pushing new data into it as it runs.

## Decision

- **A view is a page plus data.** The page is HTML the agent writes freely. Its script reads the view's data and hears each change to it, so a routine that refreshes a view changes only the data and the open page redraws without reloading. The split is taught, not enforced: a view made once may keep everything in its page.
- **The view catalogue is removed.** There is one kind of view. A view written for the catalogue stays listed and its data stays readable, but it shows as written for an older kinby until the agent writes it again. There is no converter.
- **The page runs in a frame with an opaque origin.** It has no access to the web app's cookies or storage and cannot call the hub. No second domain is needed.
- **The page reaches nothing over the network.** Its content security policy allows only libraries the hub serves, which are the theme's tokens, a base stylesheet and one chart library, and images carried inside the page. Links to `http`, `https` and `mailto` addresses open in a new tab. Forms don't submit.
- **Only the script the page was written with runs.** The web app stamps a nonce on the page's script tags when it loads the view, and the policy runs only those. Inline handlers and script that arrives inside the data never run, so data can't become code.
- **Data is written unasked, and the page is written unasked only on a turn the user starts.** A routine or a kinby agent step that creates a view or changes a page asks under the ordinary gate, with its rules and routine grants. The rule looks at what changes: writing a view whole with the same page is a data write. The approval shows the page drawn in the sandbox, with its source one click away.
- **The page is edited like a file.** `view_write` writes a view whole, `view_patch` patches its data and title, and `view_edit` replaces an exact string in its page.
- **A view keeps the errors its page last raised.** The web app records them, the agent reads them with the view, and they clear when the page or data next changes.
- **The user works a view only inside its page.** A page may filter, sort and fold what it shows. Nothing the user does in it reaches the instance.

## Considered options

- **Keep the catalogue beside the page, for now or for good.** Rejected. That is two renderers, two validators and a skill that teaches both, kept for a few views the agent can write again in one turn.
- **Any routine writes a page unasked, as it wrote a spec.** Rejected. A routine reads email and job postings, and a page is code: one that read a poisoned posting could show a fake sign-in form.
- **A CDN allowlist, as Claude artifacts use.** Rejected. A self-hosted teammate that holds the user's email shouldn't need a CDN to draw a board, or trust one.
- **Remote images.** Rejected for now. An image address can carry data out without a click.
- **Allow inline script.** Rejected. A frame can always navigate itself to an outside address, so any script that runs can send out what the page holds. A page that inserts a value from its data as HTML would let a routine's data run as script.
- **The view as a folder in the workspace, edited with the file tools.** Rejected again, as in 0108: the shell would write the page past the gate, which now tells page from data.
- **A button in a view that starts a turn, or a page that writes its own data.** Deferred. The first needs to say which thread and whether a page may speak for the user. The second makes the page a second writer of the view.

## Consequences

- The contracts lose the catalogue's models and gain the page. The web app loses its view renderer and gains the frame, its loader and the bridge that passes data and theme in and height and errors out.
- The hub serves the sandbox's libraries. Each one added is a version the `write-view` skill documents.
- The model must attach handlers from script, never as attributes. A page that breaks the rule shows up in its errors.
- The page's own script can still send out what the page holds by navigating. It is trusted because the user was present when it was written, or approved it.
- If the web app later sets a content security policy of its own, the frame inherits it, and the two must be written together.
- `write-view` becomes design guidance with whole example views. How views look now depends on that skill.
- Still deferred: history and undo, the home instance writing other instances' views, and the agent seeing its own view through the browser service.
