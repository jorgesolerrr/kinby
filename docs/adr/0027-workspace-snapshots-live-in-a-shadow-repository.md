# Workspace snapshots live in a shadow repository under `.state/`

Ticket #60 asked whether kinby should checkpoint the workspace before every turn as a hidden git ref inside the workspace's own `.git`, T3 Code style. kinby does snapshot the workspace at every turn boundary, but it stores the snapshots in a bare repository of its own under `.state/snapshots.git`, with the workspace as the work tree, instead of writing refs into the user's repository. One code path then covers every workspace kinby names, a repository or a notes folder, the "never written to by kinby on its own behalf" rule holds to the letter, and deleting `.state/` loses only kinby's history. The price is a copy of the workspace's blobs on the first snapshot, deduplicated after that.

## Considered options

- **Refs under `refs/kinby/` in the workspace's `.git`.** Free storage for tracked files and visible from the user's git tools, but it requires a git workspace and leaves kinby-owned objects in a repository the user may push, clone or garbage collect.
- **A shadow repository under `.state/`.** Chosen.
- **Copying the tree.** Exact and dependency-free, but no diff without a second tool and a full copy per turn.

## Consequences

- The image ships git. A local install without git runs with snapshots off and a warning at boot; a turn never fails because a snapshot could not be taken.
- The snapshot tree id is recorded on `turn.started` and on every closing event, so diff and revert read the event log and nothing else.
- Revert restores the whole tree as it was before the target turn, which also discards every later turn's changes. A revert is recorded as a `workspace.reverted` event and takes its own snapshot first, so a revert can be reverted.
- The gate keeps assuming no revert exists (ADR 0014). A snapshot cannot undo a push or a network call, which is what `auto` asks about.
