# A factory's ended call follows a run that ends done or cancelled

Some tickets need no change: the code already does what they ask, or an epic has nothing left to do. The software factory's `implement` step failed them twice for a branch with no commits, and the run needed a human. Nothing was wrong with the work, and the run waited for the maintainer anyway.

## Decision

**`implement` can end with the outcome `no-change`, and a factory can declare an `ended` call the hub makes once a run ends done or cancelled.**

- The coding session writes why the ticket needs no change in `.scratch/no-change.md`. `check_implementation` records it as `no_change` with the outcome `no-change`, and passes a branch with no commits only then. An uncommitted or untracked file still fails it.
- `no-change` is a `stop` outcome, so the run ends cancelled. The hub checks `done_requires` only on a done ending, so `done_requires: [merge_ready]` and ADR 0078 stay as they are.
- A factory declares `ended: { in: <instance>, call: <tool> }`, the way it declares `needs_human`. The hub makes the call when a run moves to done or cancelled, from a step's outcome, a final check or the user's cancel. The tool's parameters named like a value of the run receive that value.
- The hub records the call as owed when it moves the run, in the same write. It tries a failed call again as it does the needs_human call (ADR 0089), and a restart makes an owed call again. A run that ended before the call existed owes none.
- The software factory's `report_ended` takes `ready-for-agent` off the issue and comments the reason when the run recorded `no_change`. On any other ending it does nothing yet. It never closes the issue: closing stays the maintainer's call. The cleanup after a run that needed a human (#828) goes in the same call.

## Considered options

- **`no-change: done`.** Rejected. `done_requires` holds `merge_ready`, which only `assess` records, so the run would need a human. Applying `done_requires` only to the last step would weaken the check ADR 0078 relies on.
- **A code step after `implement` that comments the reason.** Rejected. An outcome only goes on, back, or ends the run, so every run would pass through that step.
- **The implement hook comments the reason.** Rejected. A hook records the step's result from the world. It doesn't write to GitHub.
