# An MCP server is defined as other clients define it, and the image ships Node

Amends [ADR 0111](0111-mcp-servers-are-a-library-kind-whose-calls-are-writes.md).

Grilling [#1215](https://github.com/jorgesolerrr/kinby/issues/1215), research in [code-mode-and-mcp.md](../research/code-mode-and-mcp.md). ADR 0111 gave an MCP server a definition of kinby's own: a `url` or `uvx` transport, headers that name a variable each, and a list of variable names. Claude Code, Cursor, VS Code and Gemini CLI agree on another shape, the `mcpServers` entry, and server authors publish their setup in it. A user who pastes one into kinby has to translate it by hand, and most of them start with `npx`, which the image cannot run.

## Decision

- **A library MCP server is an `mcpServers` entry.** A local server has `type: "stdio"`, `command`, `args` and `env`. A remote one has `type: "http"`, `url` and `headers`. A value may hold `${VAR}` or `${VAR:-default}`, filled from the instance's secrets, or be literal. kinby's own fields, such as the tool filter, sit beside the entry and never inside it.
- **A local server's command is a launcher the image ships:** `uvx` or `npx`. Any other command is refused, and the refusal names the two. The server still gets only the variables its entry declares, and the keys ADR 0111 refuses stay refused.
- **The image ships Node,** so `npx` servers run.
- **Servers are added from the MCP Registry too.** The hub searches `registry.modelcontextprotocol.io` and turns a `server.json` record into a definition. It prefers the record's remote over its package.

## Considered options

- **Keep kinby's shape and convert pasted entries.** Rejected. Two shapes would stay in the code and the docs, and an entry exported from kinby would work nowhere else.
- **Any command.** Rejected. A definition is new code that runs with the instance's secrets, and the launchers cover what the registry publishes for `npm` and `pypi`.
- **No Node, and point users to remote or `uvx` servers.** Rejected. Most published snippets are `npx`, so the first server a user pastes would fail.
- **A gateway in front of the servers,** such as Docker's MCP Gateway. Rejected. kinby's turn already connects to each server, and a gateway adds a container and a second policy without solving sign-in.

## Consequences

- The image grows by Node's size.
- A variable an entry names and the instance's secrets lack is pending setup.
- Definitions and picks written in the old shape are converted, and none is lost.
- The `sse` transport, which the MCP spec deprecated, is refused.
