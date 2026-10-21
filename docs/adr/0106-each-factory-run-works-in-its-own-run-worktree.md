# Each factory run works in its own run worktree

Amends [ADR 0104](0104-a-client-step-starts-on-its-runs-branch-and-never-carries-another-runs-changes.md).

ADR 0104 kept one shared workspace and switched it to a run's branch before each client step. A run that stopped with uncommitted work then blocked every other run: their steps refused to switch away from its changes. `prepare` cleaned the workspace for each new run, so it threw away whatever a stopped run had left uncommitted. And a coding session resumed by `answer` or `fix` found the files of whichever run had last used the workspace.

## Decision

**Each run works in its own run worktree, a `git worktree` of the workspace on the run's branch. A step that names a branch runs in it.**

- A client or command step declares `branch: "{{name}}"`, and the hub sends the run's value with it. The instance runs the client or the commands in that branch's run worktree, and gives its hook the run worktree as `StepEnd.workspace`. A step without `branch:` runs in the workspace, as before. Code steps name no branch: they only push and read refs, which every worktree of the clone shares.
- A run worktree lives at `/instance/worktrees/<branch>`, on the instance directory's mount. Its path never changes during its run, so a resumed session continues in the directory it started in.
- The software factory's `prepare` cuts it from `origin/<base>`, removing one already at that path, so a send back to `prepare` starts over. The workspace stays on the default branch and only fetches.
- A step whose run worktree is missing gets one from the local branch, or from `origin/<branch>`. When the workspace is on that branch, the instance first detaches it at `origin/HEAD`. When the workspace also has uncommitted changes, the step fails and names them, as ADR 0104 does. This carries the runs that were unfinished when run worktrees shipped.
- ADR 0104's switch becomes a check: a step fails before it starts when its run worktree is on another branch. It runs no hook, since nothing ran.
- The factory's ended call removes the run worktree and its local branch when the run ends done or cancelled. A branch the workspace is still on stays.
- Each run worktree installs its own `.venv` and `node_modules`. The coder recipe puts the uv and bun caches under `/instance/.state/cache`, so installs stay fast and survive an image update.

Steps still run one at a time per instance. Running several runs at once is #657, and sweeping run worktrees that outlive their run is #852.

## Considered options

- **Keep switching the shared workspace (ADR 0104).** Rejected. A stop's uncommitted work either blocks the other runs or gets thrown away.
- **A full clone per run.** Rejected. Each run would fetch every object again, and the code steps that push and read refs from the workspace would no longer see the run's branch.
- **Commit a stop's uncommitted work as WIP.** Rejected. Nothing the session didn't vouch for should land on the branch.
