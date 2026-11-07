# A tool result over the cap is saved for its turn only

Amends [ADR 0119](0119-a-shell-read-runs-unasked-and-a-routine-can-be-granted-its-code-step.md).

Spec [#1234](https://github.com/jorgesolerrr/kinby/issues/1234). Only `bash`, authored tools and MCP tools capped their output. Any other tool could put megabytes into the prompt and end the turn, and a cut with nothing kept would lose what the model needed. The turn runner now caps every result at 30,000 characters. It saves the whole output as a file in a folder for the turn under the instance's state directory, and tells the model where the file is. The gate counts a read of that folder as a shell read. The folder is deleted when the turn closes, and the instance removes any left over when it starts.

## Considered options

- **Save outputs in the workspace.** Rejected. They would show in workspace snapshots and diffs, and a coder's workspace is the user's repository.
- **Keep saved outputs for some days.** Rejected. An instance would pile up files of old output. A cleared result says to call the tool again instead.
- **A cap per tool.** Rejected. One cap in the runner covers tools that don't exist yet, MCP servers included.
