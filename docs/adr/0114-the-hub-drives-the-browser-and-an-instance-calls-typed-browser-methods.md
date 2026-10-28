# The hub drives the browser, and an instance calls typed browser methods

Amends [ADR 0113](0113-the-browser-is-a-hub-service-and-sign-ins-never-enter-the-instance.md).

Codex's security review of [#965](https://github.com/jorgesolerrr/kinby/pull/965) found that the vault leaks. The instance runs the Playwright client, and the hub's browser route relays its protocol to the browser service, refusing calls outside an allowlist and replacing filled values in the frames it sends back. `bash` inherits `KINBY_CONTROL_TOKEN`, so a script can lease a browser and speak the protocol itself. It fills a password into a text field and reads it back from a screenshot, or presses Backspace and reads what is left in a snapshot. The filter matches only the whole value, so a partial or encoded value gets through. An output filter cannot close this, because a page can echo a value in too many ways.

The instance already reaches the browser through one seam, the `BrowserService` Protocol: open, run an action, snapshot, text, screenshot, close. Only its adapter sits on the wrong side.

## Decision

- **The hub runs the Playwright client.** It connects to the browser service and keeps one context per instance and turn. Nothing outside the hub speaks the Playwright protocol, and the route that relayed it goes away.
- **An instance calls typed browser methods on the hub**, one per `BrowserService` method, with its control token. The hub binds each call to the instance the token names. The browser tools and the gate do not change. A script that holds the token can still act in the browser outside the gate, but it can read only what those methods return.
- **A vault fill is a hub method, and the hub checks the field.** A password fills only an `input[type=password]`, which a snapshot or screenshot shows masked. A username fills only a field that is not a password field. `browser_type` still refuses password fields, and no method runs page JavaScript.
- **The hub hides every filled username and password in what it returns**: the URL, the title, text, the snapshot and download names. A sign-in form sent by GET puts its values in the URL.
- **The hub closes a turn's context itself** when the instance closes it, when it is idle too long, or when the instance stops, so a lost instance cannot hold one of the service's browsers.
- **An upload carries the file's bytes in the call, and a download comes back the same way**, under a size cap. Paths stay workspace paths on the instance side.
- **A takeover's live view runs between the web app and the hub.** The hub owns the page, so it streams the screencast and takes the user's input itself. The instance hears only that the takeover finished, so its turn resumes. A takeover started from the instance page to create a profile never reaches the instance.

## Considered options

- **Keep the relay and tighten its filter.** Rejected. Every allowed call that reads the page is a channel, and a script can split, encode or draw a value.
- **Keep the relay and fill only password fields.** Rejected. The protocol still lets a client change what the page shows, and the gate still sees one connection rather than each action.
- **Stream the takeover through the instance.** Rejected. `call_hub` answers once per call, so it would need a streaming call on the hub route for frames that start and end on the hub.
- **Merge the vault switched off, and redesign later.** Rejected. It writes #965's fill path twice, once on the relay and again on the hub.

## Consequences

- The instance image drops Playwright. Only the hub runs it, next to the browser service it already carries.
- The hub's contract gains the browser methods, and the hub's process holds every open context, so the hub's memory grows with the cap of 3 browsers.
- A TOTP code is not hidden. It is used once and lasts 30 seconds, and six digits can match unrelated text.
- A page on the entry's own site can still show a value the form sent. That is the site's to prevent, as it is for any browser.
