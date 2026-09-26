# Subscription logins are URL-and-code only

A package declares each subscription login as a command, a volume, and a pattern that finds a URL and a one-time code in what the command prints. The hub runs the command in a setup container on the instance's image and shows the URL and the code on the login's operation. The user opens the URL in any browser and enters the code. The hub relays no terminal (ADR 0049), so a login that needs anything typed back into the command cannot run from the app.

Claude Code is such a login. `claude setup-token` prints a URL and then waits for a code pasted back into its terminal. Its token is therefore a secret setup field, and the field's description tells the user to run `claude setup-token` on their own machine. Codex signs in with `codex login --device-auth`, which only prints, so it is a subscription login.

Decision agreed during [Contract for the create flow](https://github.com/jorgesolerrr/kinby/issues/303).

## Consequences

- A login waits for the user for up to 15 minutes. It runs beside the instance's lifecycle operations and takes no lifecycle lock, so the instance starts and stops meanwhile. Removal and deletion refuse while a login runs, so the setup container never writes into storage that is being taken away.
- After 15 minutes the hub removes the setup container and fails the login as expired. There is no cancel; starting the login again while it runs returns the running one, with the same code.
- The pattern is matched against everything the command printed so far, so a URL and a code on separate lines are found together.
- A login's volume is named after the instance and the login id. The factory's `codex` login gets the name the hub gave the Codex volume before packages declared logins, so existing instances keep theirs.
