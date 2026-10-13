# Repository commands belong to the instance and are recorded under the ordinary gate

Follows [ADR 0090](0090-an-instance-picks-its-factory-role-and-owns-its-runs.md).

The shipped software factory spelled kinby's own `uv sync`, `bun install` and `bun run check` in its `setup` and `checks` steps. ADR 0090 says a factory names no repository's commands, and a `command` step could read commands from nowhere but the factory file. A coder for another repository had no place to keep its own ([#595](https://github.com/jorgesolerrr/kinby/issues/595)).

## Decision

- **The instance holds its repository commands.** They are named lists in the instance, next to the repository it works in. A `command` step names a list, and kinby expands it in place when the step runs. The shipped factory names two, `setup` and `checks`.
- **A repository survey proposes them, on demand.** The user asks the coder in a thread. The coder reads the repository, runs each proposed command in a temporary worktree on the default branch, and proposes the lists with each result.
- **The instance tool records them, under the gate as any other write.** The approval shows the whole list that will run. No rule forces the ask under full-access mode.
- **Commands only.** The survey reports a toolchain or service the image lacks as text. It changes no image and starts no service.
- **A missing list stops work at intake.** The hub refuses a work item for an instance that lacks a list its factory's steps name, and the instance shows it as pending setup. An empty list is a recorded answer and runs nothing.

## Considered options

- **A file in the repository, read from the default branch**, with the merge as the approval. Rejected. Every repository a coder works in would carry a kinby file, work repositories included, and the commands are one coder's way of working there.
- **A copy of the factory per repository.** Rejected by ADR 0090.
- **A forced ask whatever the permission mode.** Rejected. The checks already run scripts the repository defines, from the agent's branch, with the coder's secrets, and ADR 0073 limits tickets to trusted authors. A full-access coder can run any command already. The approval adds a human look at the command lines, and a special case in the gate would protect nothing more.
- **The recording tool proves the commands** on a clean checkout before it writes them. Rejected. A red default branch would block recording, and one tool call would last as long as the whole check suite.
- **The survey also proposes image changes and services.** Rejected for now. kinby ships the image recipes, and a service the coder starts is a separate trust decision.
- **Carrying kinby's three commands into existing coders.** Rejected. They are wrong for every repository but kinby.

## Consequences

- kinby cannot say a recorded command ever passed. A wrong setup command sends the first run to needs human with the failing command. A wrong check command spends two check repairs first.
- A coder created before this takes no work until it is surveyed once.
- CEC-Agent's test Postgres and MinIO still run from a compose file written by hand on the box.
- A step reads its list when it runs, so a run in flight uses a list changed after it started, and a needs-human run can be retried after a command is fixed.
- The web app shows a coder's repository commands and does not edit them. Changes go through the coder.
