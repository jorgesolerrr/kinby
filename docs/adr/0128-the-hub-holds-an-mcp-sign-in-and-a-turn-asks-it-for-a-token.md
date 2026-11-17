# The hub holds an MCP sign-in, and a turn asks it for a token

Follows [ADR 0113](0113-the-browser-is-a-hub-service-and-sign-ins-never-enter-the-instance.md).

Grilling [#1215](https://github.com/jorgesolerrr/kinby/issues/1215), research in [code-mode-and-mcp.md](../research/code-mode-and-mcp.md). Cockpit's MCP server accepts OAuth sign-ins only, as do Sentry's, Linear's, Notion's and GitHub's. A library MCP server could send fixed headers only, so a token pasted by hand expired within hours and an overnight routine failed by morning. The MCP authorization spec says how a client signs in. What it leaves open is who in kinby runs the sign-in and where the refresh token lives. Anything under `/instance` is readable through bash.

## Decision

- **The hub runs the sign-in.** The user starts it from the web app, finishes it in a browser, and the redirect lands on a hub route. The hub follows the MCP authorization spec: it finds the authorization server from the MCP server's own metadata, so a definition declares no OAuth. It registers through dynamic client registration, or with a client id the definition gives for a server without it.
- **A sign-in belongs to one pick.** Two instances that pick the same server sign in once each, so one instance never acts under an account the user gave another.
- **The hub keeps the client registration and the tokens,** encrypted with its own key, and never shows them. It renews the access token.
- **A turn asks the hub for an access token** on its hub route when it connects to the server, and keeps the token in memory only. The refresh token never enters the instance.
- **A sign-in that is not done is pending setup.** One the server stops accepting goes back to pending and is an instance notice. Turns run without that server until the user signs in again, so a routine fails soft.
- **Nothing starts a sign-in from a turn.** A turn that meets a missing sign-in reports it and goes on.

## Considered options

- **The instance runs the flow with the MCP SDK and keeps the tokens in its secrets.** Rejected. The agent could read the refresh token through bash, and instance secrets reach a container only when it is recreated.
- **The hub proxies every MCP call,** so the instance sees no token at all. Rejected. Every MCP byte would cross the hub, which would become a second place that decides what a call may do. A leaked access token lasts minutes to hours.
- **One sign-in per library entry, shared by every instance.** Rejected. A work account would reach a personal instance without the user choosing it.
- **Client ID Metadata Documents now.** Deferred. The document must sit at a public HTTPS URL and list the redirect, which differs for every self-hosted hub. Dynamic registration covers Cockpit and the servers checked.

## Consequences

- The hub gains a sign-in store, a redirect route, and a token method on the instance's hub route.
- An instance without its hub route cannot use a signed-in server.
- Dropping a pick or deleting its instance deletes the sign-in.
- A server whose refresh tokens are short-lived sends the user back to sign in as often as they expire.
