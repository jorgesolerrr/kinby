# The hub owns channels, and an instance notifies only when it chooses to

Amends [ADR 0021](0021-routine-checks-without-work-skip-models.md), [ADR 0022](0022-scheduler-disables-repeatedly-failing-routines.md), [ADR 0063](0063-the-hub-aggregates-usage-live-and-stores-none.md) and [ADR 0096](0096-the-home-instance-asks-the-hub-and-the-hub-checks-the-approval.md).

Ticket [#640](https://github.com/jorgesolerrr/kinby/issues/640): kinby never tells the user anything. A failed routine, a run that needs human, or a spec waiting for review is found only by opening GitHub or the web app. ADR 0021 left "a successful model turn with no outgoing message" to this work, and ADR 0022 left `routine.list` and the REPL as the stopgap for routine notices. kinby now sends **notifications** through **channels**: Telegram, Discord and email first.

## Decision

- **The hub owns every channel.** One user has one Telegram bot, not one per instance. The hub already relays approvals, holds factory runs and their stops, reports instance notices, and keeps tokens as hub secrets. A channel's settings live in the hub's SQLite and its tokens as **hub secrets**. A notification an instance raises while its hub is stopped goes out when the hub is back.
- **A channel kind is a plug-in.** A class behind a small Protocol: send a notification, and turn an inbound call into a **reply** when the kind supports one. The hub finds kinds through a `kinby.channels` entry-point group, as the core finds tools (ADR 0010), so a new kind touches nothing in the core. kinby ships `telegram`, `discord` and `email`, the last over Resend. A channel is a configured kind, set up from the web app.
- **kinby notifies by itself on what needs the user**, a fixed list in code: an approval waiting, a routine notice (first failure, automatic disable), a factory run that **needs human**, and an instance notice. Good news, such as a run done or a pull request opened, is not on the list.
- **An instance notifies only when it chooses to.** `notify(title, text, options?)` is a tool on every turn origin. A turn that does not call it is silent, which settles ADR 0021's silent turn. The agent's `text` says what happened and why it is telling the user. The hub adds a header the agent cannot change, with the persona name and the routine or thread title, and a link back. `notify` is allowed in every preset under its own rule, which `permissions.toml` can tighten, so a routine never parks to tell the user something. Each instance has a daily cap, 20 to start; a call past it fails, and kinby sends one notification about it.
- **Every notification goes to every enabled channel that has not muted its instance.** The agent never picks a channel. Each notification offers a "Mute this instance" option, and the web app edits each channel's mute list. There are no severity levels.
- **Replies are options, not free text, for now.** On kinby's own notifications, an approval offers approve and deny, and a run that needs human offers retry, send back and cancel. A routine notice and an instance notice carry only a link. A reply to an instance's notification starts an ordinary user turn in the thread that sent it, with the option as the message. The sending turn has already ended; nothing parks waiting for the user. The hub checks each reply against current state: a stale one changes nothing, and the hub edits the channel message to show what happened, as it does for one that worked.
- **Replies come only from the paired user, and email carries no actions.** The user pairs a Telegram or Discord channel by sending it a code the web app shows, and the channel answers only to the first sender of that code. Email carries links into the web app, whose cookie session does the acting. A link that acts by itself would be a bearer token for whoever holds the email.
- **Replies are recorded, then applied.** The hub's public entry point takes Telegram's webhook (checked by its secret-token header) and Discord's interactions endpoint (checked by its Ed25519 signature). It records the reply and answers at once, as the receiver records a delivery (ADR 0025), which also meets Discord's three-second deadline.
- **The hub keeps an outbox.** Unlike usage (ADR 0063), sending once needs a durable record. The hub keeps one row per notification and channel in its SQLite, keyed by the notification's source (an instance and the event that raised it, or a run and its stop). Each row ends sent or failed. The hub retries a row with backoff for a day, and a restart resumes the pending ones. Email uses Resend's idempotency key. A notification that failed on every channel shows in its thread as not delivered.
- **Bodies hold no transcript.** kinby's own notifications send a title, one line and a link. An instance's notification sends the text the agent wrote, under the agent's gate rule.
- **The sidebar's "needs you" gains one case.** A routine run with a notification whose options are still unanswered shows in the sidebar, derived from its events like the other cases. There is no notification inbox.
- **The home instance reads channels and mutes.** `hub:home` gains reading channels and their mute lists, and muting or unmuting an instance, asked like every hub write. Creating a channel or touching its tokens stays in the web app, since it sets a secret.

## Considered options

- **Each instance owns its channels, as a plugin.** Rejected. Every instance would hold its own bot and token, and the user would configure each channel once per instance.
- **The home instance sends for everyone.** Rejected. Telling the user that a run needs them would cost a model turn.
- **Triggers declared per instance or factory.** Rejected. The fixed list mirrors the sidebar's rule, and anything else is the agent's call through `notify`.
- **A reply parks the turn, as an approval does.** Rejected for now. A routine turn would hold its wait for hours. A separate tool that waits can come later if a step needs one.
- **Gmail through its API.** Rejected. It needs an OAuth flow. An email channel over Resend takes an API key, which fits a hub secret.

## Consequences

- Free-text replies, editing channel messages when the user answers in the web app, and per-routine mutes are deferred.
- `routine.list` and the REPL stop being the only way to learn about a routine notice.
- [#119](https://github.com/jorgesolerrr/kinby/issues/119)'s reminder fires a turn that calls `notify`, so a reminder names no channel.
- Discord pairing assumes the bot shares a server with the user, so it can open a DM.
- Resend's free plan caps sending at 100 emails a day, and sending to anyone but the account's own address needs a verified domain.
