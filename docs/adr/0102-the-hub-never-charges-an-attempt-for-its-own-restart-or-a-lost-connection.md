# The hub never charges an attempt for its own restart or a lost connection

Amends [ADR 0075](0075-factories-are-hub-run-and-own-their-runs-state.md) and [ADR 0076](0076-a-steps-result-comes-from-its-hook-and-the-next-step-checks-its-inputs.md).

A step attempt the hub stopped during ends as interrupted, and an interrupted attempt spends a retry. Command and code steps get no retries by default, so a hub restart during `checks`, `publish` or `assess` sends the run straight to needs human. A control connection that closes before the instance answers ends the attempt as failed, though the instance may still be running the step.

Both happened on 2026-10-18. A hub restart stopped kinby #772 at `checks` with its implement commit safe on the branch. Three tickets were blocked behind it, so the coder took no new work for about 13 hours. A closed control socket stopped #770 at `assess` with its pull request open and green. Neither stop said anything about the work.

## Decision

**The instance says how a step ended, never the hub's own state.**

- **A hub restart is no attempt.** An attempt the hub stopped during is not counted, like a call the instance never took. When the hub comes back, it offers the step again.
- **A lost connection decides nothing.** When the control connection closes before the instance answers, the hub asks the instance for that call's outcome once it reaches it again. It takes the outcome the instance holds, and offers the step again only when the instance does not hold the call. How the instance keeps a call's outcome across a lost connection belongs to the control transport.
- **Steps with outside side effects are safe to run twice.** A step can now run again without the user deciding it, so `publish`, `assess` and any step like them check what already exists before they write: an open pull request, a posted comment, a label.

Retry budgets are unchanged. A failed attempt still spends one, and a timed-out one is still never retried.

## Considered options

- **One retry by default for command and code steps.** Rejected. It spends a retry the work did not earn, a second restart still stops the run, and the step still has to be safe to run twice.
- **Treat a lost connection like a restart and offer the step again.** Rejected for `client` steps. A coding session still running on the instance would race its own retry on one worktree.
- **Leave lost connections to the control transport.** Not taken alone. The transport decides how an outcome survives a lost connection, but the rule that a lost connection never fails an attempt is the factory's.
