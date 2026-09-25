# Subscription use is counted from delegated runs

Kinby counts subscription use from the **delegated runs** that tools report, and does not read the subscriptions' own meters. A tool calls `report_run` on its `ToolContext` after each run of an outside agent. Core records a `run.delegated` event in the current turn, with the **usage source**, tokens, duration, client turns, and outcome. Stats read those events the way they read model calls ([ADR 0018](0018-statistics-and-evals-derive-from-the-event-log.md)). A run the plan refused is recorded as limited, with the time its **plan window** resets.

Neither CLI's non-interactive output says how much of a plan window is used. Claude Code shows the 5-hour and weekly percentages only in the status line's JSON input. Codex exposes them through its app-server, not through `codex exec --json`. Reading the meters would mean driving those interactive surfaces, which are not documented as stable. So kinby shows its own runs and tokens per source over the last five hours and seven days, without a percentage. Use outside kinby does not appear. A limited run is the one moment the user needs to know about, and the clients do report it.

Core does not parse a package's own report, such as the factory's pipeline report. Every package reports through the same hook, and core never depends on one package's output format. The package reports each run on its own, not the client's running total, so it subtracts the previous reading when it resumes a Codex thread.

Delegated runs sit beside a turn's token usage, never inside it: the turn stays the sum of its model calls ([ADR 0026](0026-model-calls-and-gate-decisions-are-events.md)), and budgets stay API-only. A time range places a run by its own timestamp, not its turn's close, because a long turn can span two windows.

Decision agreed during [Usage accounting across the API and the two subscriptions](https://github.com/jorgesolerrr/kinby/issues/219).
