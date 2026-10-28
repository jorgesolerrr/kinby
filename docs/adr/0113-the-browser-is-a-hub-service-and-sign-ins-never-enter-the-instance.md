# The browser is a hub service, and sign-ins never enter the instance

Amends [ADR 0077](0077-an-instance-reaches-the-hub-only-on-its-own-intake-route.md). Amended by [ADR 0114](0114-the-hub-drives-the-browser-and-an-instance-calls-typed-browser-methods.md): the hub runs the Playwright client, an instance calls typed browser methods, and a takeover's live view runs between the web app and the hub.

Ticket [#161](https://github.com/jorgesolerrr/kinby/issues/161): kinby should do autonomous jobs on websites, including signed-in ones. The research in [#920](https://github.com/jorgesolerrr/kinby/issues/920) measured headless Chromium at about 820 MB of image and up to about 700 MB of memory per page, and found that browser-use and Stagehand run their own model loop, which hides each action from the gate. The research in [#922](https://github.com/jorgesolerrr/kinby/issues/922) found that Vercel's agent-browser uses the same snapshot-and-refs model as Playwright, with no Python library, and takes over through a screencast rather than noVNC. Anything stored under `/instance` is readable through bash, so a cookie or password kept there is one the agent can read.

## Decision

- **One browser service on the hub serves every instance.** The hub runs Playwright's browser server on the headless shell as its own container, starts it when first needed, and stops it when idle. An instance whose manifest sets `browser = true` asks the hub for a browser at the start of its first browser call. It gets an address and a token that last one turn. The service caps how many browsers run at once (3 on the playground); a turn over the cap waits, then fails with a clear message.
- **Every turn gets a fresh browser context.** Nothing is shared between turns or instances, and the context closes when the turn ends.
- **The service reaches the internet only.** It sits on its own Docker network and refuses loopback, private and link-local addresses, so a page cannot make it reach the hub, an instance or the user's network.
- **One typed tool per action, so the gate sees each one.** Reads: `browser_snapshot` (interactive elements only by default, with a character cap), `browser_text`, `browser_screenshot`, `browser_scroll`, `browser_wait`. Writes: `browser_click`, `browser_type`, `browser_select`, `browser_press`, `browser_upload`. `browser_open` follows the URL rule of [ADR 0110](0110-web-reads-ask-only-for-a-url-the-model-built.md). A ref fails rather than guess when its match is ambiguous. There is no tool that runs page JavaScript and no batch tool. Downloads land in the workspace's `downloads/`, and uploads take only workspace paths.
- **Browser profiles live in the service, kept per instance.** A profile is a named signed-in state. The agent opens a page with a profile by name, and the context's state is saved back to it when the turn ends. The agent never reads, exports or deletes one; the user manages profiles from the instance page.
- **A takeover hands the browser to the user.** `browser_takeover(reason)` makes the turn wait, needing the user, as an approval does: it shows in the sidebar and in channels. The web app shows a live view the hub relays from the service's screencast, which takes the user's mouse and keyboard. The user finishes, optionally saving a profile, and the turn resumes. The user can also start a takeover from the instance page to create a profile.
- **The vault fills sign-ins without the agent seeing them.** Each instance has a vault on the hub: credentials bound to one site, each with a username, a password and an optional TOTP seed, stored encrypted and never shown again. The model sees names and sites only. `browser_fill_credential(ref, name, field)` makes the service type the value into the page, only when the page's site matches the entry's. It is a write under the ordinary gate, which a **routine grant** can cover. `browser_type` refuses password fields, so the vault is the only way a password is typed. Vault entries are not **hub secrets**: they are never written into `.env` or the instance.
- **An instance reaches the hub's browser route as it reaches intake**, with its control token, and the hub binds the browser, its profiles and its vault to the instance the token names.

## Considered options

- **Chromium in an opt-in second image, one browser per instance.** Rejected for v1. A small box like the playground cannot spare 700 MB per instance, and profiles in the instance would be readable through bash.
- **browser-use, Stagehand or agent-browser's chat loop.** Rejected. Each runs its own model loop, so the gate would see one call for many actions.
- **agent-browser as the engine.** Rejected. It has no Python library, it would need a subprocess per action, and it is 0.x with releases every few days. Its read/write split, interactive-only snapshots, failing refs and screencast takeover are taken anyway.
- **Profiles in the instance.** Rejected. Bash reads `/instance`.
- **Sign-ins only by takeover, with no vault.** Rejected. An unattended job whose session expired would wait for the user every time.
- **Payment cards in the vault.** Rejected for v1. A payment needs a confirmation every time, not one gate check.

## Consequences

- The hub gains a container to run, a browser route, profile and vault stores, and a relay for the live view. The vault's encryption key belongs to the hub.
- One browser serves every instance, so a crash in it fails every running browser turn at once.
- Taking over from a phone through a channel is deferred. The live view needs the web app.
