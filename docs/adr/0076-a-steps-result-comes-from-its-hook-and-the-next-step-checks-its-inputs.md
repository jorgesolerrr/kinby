# A step's result comes from its hook, and the next step checks its inputs

A **step result** is never read from an agent's own report. When a **step** ends, however it ended (clean, malformed output, timeout, killed), kinby runs the step's **hook**: deterministic code in the step's **instance** that reads the world (git, GitHub, files the step wrote in the workspace) and records the result's values. The next step declares the values it `requires` and checks them before it starts. A missing or false one fails the step that should have produced it, which is then retried under its own `retry`. The factory's `done_requires` checks the last step. Every `agent` and `client` step names a hook. `command`, `code`, `wait` and `approve` steps produce their results directly.

A hook is an instance plugin like a **tool** or a **skill**, never offered to the model. kinby ships default hooks, and an instance can hold its own.

The coder's failures drove this. Since 09-25, 8 of its 16 failed runs were Claude ending its turn with a background task still running, which the coder read as an invalid result. The work was done in those runs, and a rerun fixed 6 of the 7 relabeled.

## Considered options

- **The agent ends with a structured result naming its outcome, and a malformed one fails the attempt.** Rejected. Parsing an agent's output stream was the coder's main failure. A run that opened its PR and then printed a malformed result would fail.
- **The hub runs evidence checks after each step.** Rejected. It works, but it adds a separate phase to the hub. The next step needs its inputs anyway, so checking them there costs no extra code and no tokens.
- **The coding client's native hook records the result** (Claude Code's Stop hook, Codex's `notify`). Rejected. They differ per client, and a timed-out or killed process fires neither.

## Consequences

- An outcome only the agent can judge, such as a review's verdict, is written to a file in the workspace for the hook to read. A file on disk counts. A line in stdout does not.
- The agent's last message is kept as the step's human-readable summary only.
- A timeout is never retried. Any other failed attempt follows the step's `retry`: 1 by default for `agent` and `client` steps, 0 for steps with outside side effects unless they opt in.
- The implement step's hook checks that the branch has commits ahead of its base. The coder's implement path never checked that the work was committed.
