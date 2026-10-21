# A client step starts on its run's branch and never carries another run's changes

Amends [ADR 0075](0075-factories-are-hub-run-and-own-their-runs-state.md). Amended by [ADR 0106](0106-each-factory-run-works-in-its-own-run-worktree.md): each run works in its own run worktree, and the switch is a check that the run worktree is on its branch.

All runs of one instance share its workspace and run one step at a time. Between two steps of a run, another run's steps can move the workspace to their own branch. The software factory's command steps `setup` and `checks` start with `git switch {{branch}}`, but its client steps asked the model to switch in their prompts. When the prompt also said "nothing failed: change nothing and stop", the model skipped the switch. Then `check_implementation` found the workspace on the other run's branch and the run needed a human. This stopped #818 and #820 at `fix` on the playground.

## Decision

**A client step can name its run's branch, and the instance puts the workspace on it before the client starts.**

- A client step declares `branch: "{{name}}"`, the run value that holds the branch. The hub sends that value with the step, and requires it like a value a command names.
- On the branch already, the workspace stays as it is, uncommitted changes included: they are this run's.
- On another branch with no uncommitted changes, the instance runs `git switch <branch>`.
- On another branch with uncommitted changes, the step fails before the client starts and names them. The workspace stays where it is, so those changes are never discarded and never carried onto this run's branch.

The software factory's `implement`, `answer` and `fix` declare `branch: "{{branch}}"`, and their prompts no longer ask for a switch.

## Considered options

- **Keep the switch in the prompts, worded more firmly.** Rejected. Whether the step runs on the right branch should not depend on how the model reads a prompt.
- **A list of commands a client step runs first, like a command step's `run`.** Rejected. Plain `git switch` carries uncommitted changes that don't conflict onto the new branch, and a command list cannot say "fail when dirty unless already on the branch" without a shell.
- **Discard what another run left, as `prepare_branch` does.** Rejected for steps after `prepare`. Uncommitted changes there may be the only copy of a run's work.
