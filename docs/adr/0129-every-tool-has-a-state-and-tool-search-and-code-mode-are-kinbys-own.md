# Every tool has a state, and tool search and code mode are kinby's own

Amends [ADR 0111](0111-mcp-servers-are-a-library-kind-whose-calls-are-writes.md).

Grilling [#1215](https://github.com/jorgesolerrr/kinby/issues/1215), research in [code-mode-and-mcp.md](../research/code-mode-and-mcp.md). Every tool an instance holds sends its definition on every model call. An MCP server's `include` list and the manifest's list of switched-off default tools were the only ways to hold that down, and both are all or nothing. Anthropic measured 77K tokens of definitions falling to 8.7K with tool search, and 37% fewer tokens when the model scripts its tool calls. The MCP project's Client Best Practices describes both patterns, and Anthropic, OpenAI, Cloudflare, goose and Pydantic AI each ship a version.

## Decision

- **Every tool has a state: loaded, searchable or off.** Tools are switched in tool groups: each MCP server, the default tools, the memory tools, the browser tools and the authored tools. A tool takes its group's default unless it has its own state. For an MCP server the library entry sets the defaults and the pick holds the instance's changes. This replaces `include` and the manifest's list.
- **Only the user and the home instance change a state.** An instance never changes its own, and a change applies from the next turn.
- **Tool search is three fixed tools:** search the searchable tools, read one's definition, and call one by name. kinby searches by keyword over names and descriptions. The three tools never change while tools come and go, so the prompt cache holds.
- **Code mode is one tool, `run_code`.** The model writes Python, which runs in Monty inside the turn's process and reaches nothing but the turn's tools.
- **The gate checks every call as a call to the real tool,** whether the model made it directly, through tool search, or from a script. A routine grant and a trusted read-only hint hold in all three. A call from a script that the gate would ask about fails inside the script, and the model makes it directly.
- **A script reaches only the tools it can call unasked.** A tool the gate asks about on every call, whatever its arguments, is left out of scripts, and a turn with no tool left offers no `run_code`. A tool whose answer turns on its arguments, such as bash or a URL read, stays in, and its asked calls fail as above ([#1446](https://github.com/jorgesolerrr/kinby/issues/1446)).

## Considered options

- **Each provider's own tool search and programmatic calling.** Rejected as the base. kinby runs Anthropic and OpenAI models and the two differ. Anthropic's programmatic calling runs the code in Anthropic's container and drops a pending call after about 4 minutes, which is shorter than an approval can wait.
- **kinby decides what to load from a token threshold.** Rejected. The user would not know which tools a turn sees, and the threshold would need tuning per model.
- **Search by embeddings.** Deferred. Tool descriptions are short and full of keywords, and an index would need rebuilding whenever tools change.
- **TypeScript in Deno or a V8 isolate,** as Cloudflare does. Rejected. It adds a second language and runtime to a Python project.
- **Run the script in the instance's container with bash.** Rejected. The script would hold the instance's secrets and the network, and its calls would skip the gate.
- **Pause the script while an approval waits.** Deferred. An approval can wait for hours, and a paused interpreter would have to survive a restart.

## Consequences

- A turn's loaded tools can be few while hundreds stay reachable.
- A searchable tool costs the model a search before its first call, so a tool used on most turns belongs loaded.
- Monty runs a subset of Python and its docs call it experimental. A script it cannot run fails, and the model falls back to direct calls.
- A script that fails midway leaves the writes it already made, and its result lists them.
