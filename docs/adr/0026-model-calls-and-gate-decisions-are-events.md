# Model calls and gate decisions are events

Ticket #12 chose to widen the event log rather than add a second record for observability. Every model call appends a `model.completed` event carrying the model name, input and output tokens, the cache read and cache creation counts, and its duration. Every tool call the model requests appends `tool.call`, then a `tool.gated` event holding the gate's final verdict (allow or deny, the rule, and whether policy or the user decided), then `tool.result` for calls that ran. `tool.call` carries the tool's write flag and `tool.result` its duration. `turn.started` carries a prompt version: a hash of the rendered system prompt without the environment block. The closing events keep their token totals, and `turn.failed` and `turn.interrupted` gain them, so a turn that died after several model calls costs what it cost.

Navigation, the read-only tool calls and the model reasoning a turn spends locating what it needs in the workspace before acting, is derived from those events by turn metrics and reported by `kinby stats`. Its trend for one workspace is the measure of whether memory is teaching the agent the workspace.

Tracing stays local: the event log and the SQLite checkpointer are the trace. kinby ships no tracing integration. Exceptions and rejected calls go to stderr through the standard logging module, configured once by the CLI, never to a file under `.state/`.

## Consequences

- Tokens live in two places by design: per call in `model.completed`, per turn on the closing event. The closing total is the summary budgets, `usage.get` and the recap read; the per-call events are the detail navigation reads. Turn metrics compare the two and warn when they differ.
- Denies are no longer read from the text of a tool result. `tool.call` is emitted for every call the model requests, so a denied call is visible as a call followed by a deny, and an approval the user refuses is a deny decided by the user.
- Statistics classify reads and writes from the log alone. A tool renamed or removed later still classifies correctly in old logs.
- The price map gains optional cache read and cache write prices. A model without them prices cached tokens at its input rate, so every existing entry stays valid.
- The prompt version excludes the environment block on purpose: that block changes with the date and working directory, and the version exists to group turns by the prompt the user wrote.
- A self-hoster who sets LangChain's own tracing variables gets whatever LangChain does. kinby neither enables nor documents it.

## Considered options

- Per-turn totals only, with no per-call event: rejected because navigation needs the tokens spent between tool calls, and a crash between calls loses everything.
- Per-call events as the only token record, with closing events summing nothing: rejected because budgets, the no-work path and the recap already read the closing totals, and a summary next to its detail is an ordinary event-log shape.
- Gate verdict as fields on `tool.result`: rejected because a denied call has no result to carry it and the user's approval answer would still be invisible.
- An OpenTelemetry exporter behind a manifest flag: rejected because nothing today needs spans the log does not already hold, and the map rejected hosted tracing for self-hosting.
