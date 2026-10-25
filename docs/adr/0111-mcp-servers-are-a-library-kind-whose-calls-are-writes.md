# MCP servers are a library kind whose calls are writes

Amends [ADR 0091](0091-the-hub-keeps-a-library-and-a-pick-is-a-pinned-copy.md).

Ticket [#161](https://github.com/jorgesolerrr/kinby/issues/161): kinby has no MCP client, and ADR 0091 left MCP servers as a future library kind. The research in [#918](https://github.com/jorgesolerrr/kinby/issues/918) and [#919](https://github.com/jorgesolerrr/kinby/issues/919) found that every agent studied leaves MCP configuration to the user, that Hermes asks on every call to an untrusted server's tools unless the tool declares `readOnlyHint`, and that Goose refuses dangerous environment keys for local servers. An MCP server becomes a **library** kind.

## Decision

- **An MCP server is a library kind.** Its definition holds its transport (a remote URL, or a `uvx` command run inside the instance's container), the environment variable names it declares, and an include filter on its tools. An instance picks it like a skill, as a pinned copy.
- **Only the user or the home instance adds one.** The user adds one from the web app. The home instance adds one through a hub tool that asks in every mode (ADR 0096). An instance never adds an MCP server to itself, and no instance tool touches its picks.
- **Every call to an MCP tool is gated as a write.** The server's `readOnlyHint` is ignored until the user trusts that server's hints, a choice made on the pick and recorded, which the user can undo. It is the same rule as an **authored tool**: kinby never trusts a write flag that someone else declared.
- **A local server gets only what it declares.** Its environment holds only the variables it declared, filled from the instance's `.env`, and keys such as `PATH`, `LD_PRELOAD` and `PYTHONPATH` are refused in a declaration.
- **Remote and `uvx` servers only in v1.** The image has no Node, so `npx` servers are out.

## Considered options

- **MCP config in the instance, written by the agent.** Rejected. Every system studied keeps it away from the agent, and a new server is new code running with the instance's secrets.
- **Trust `readOnlyHint` by default.** Rejected. The server declares it, so it is the self-declared flag again.
- **Add Node to the image.** Deferred. The image would grow for servers that have `uvx` or remote equivalents.

## Consequences

- The tool registry gains MCP tools at turn start, named by server, inside the include filter. A server with many tools costs context, which the filter keeps in hand.
- The library's validation gains the MCP server kind, and the home instance's picks cover it.
