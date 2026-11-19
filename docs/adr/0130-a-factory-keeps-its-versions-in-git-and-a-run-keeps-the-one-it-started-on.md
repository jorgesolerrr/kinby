# A factory keeps its versions in git, and a run keeps the one it started on

Decided while grilling [#1384](https://github.com/jorgesolerrr/kinby/issues/1384). Research: [Warp factories](../research/warp-factory.md).

Until now the hub kept one version of each factory, the last that passed the factory check, and a run read it as it was at each step: a step ran the factory's tools "as they are then". Once the user changes steps from the web app's step editor, and from chat, a save is no longer a rare hand edit. The user needs to see what changed, undo it, and take kinby's later version of a shipped factory they changed. A run that has waited thirty times in babysitting shouldn't change its behavior halfway through because a step was edited for another line.

## Decision

- **The hub's factories folder is a git repository.** Every save that passes the factory check is one commit, which is a **factory version**: a save from the step editor, an approved change from chat, a revert, or a change made by hand in a factory's folder, which the hub commits the next time it loads the factory. History lists them with their diff and where each came from. A revert or "reset to shipped" is a new version, never a rewrite of history.
- **A run keeps the version it started on.** Its steps read the factory file, prompts, tools and checks as they are in that version, and each step attempt names it. A run moves to the latest version only when the user retries a run that needs human, or sends it back, and chooses to. So the step that just failed can be edited and retried on the same run.
- **A shipped factory the user changed follows kinby by a merge.** The version kinby shipped is kept apart from the user's changes. On a kinby update the hub merges the new shipped version into the user's. A clean merge that passes the factory check lands by itself. Otherwise the user's version stays and is marked as having a shipped update waiting, and each step that differs offers kinby's version to take.

## Considered options

- **Live edits, as today.** Every step reads the latest factory. Rejected: a run would mix two versions of the same factory, and a change made for one line would reach runs of every other line halfway through.
- **Pinning with no way to move.** This is Warp's model. Rejected: editing the step that failed couldn't fix the run that failed, which is the edit the user makes most.
- **Numbered snapshots of the folder.** Rejected: we would rebuild diffs, history and the three-way merge that git already does.
- **No merge for shipped updates, only a "differs from shipped" diff.** Rejected: a step the user never touched would stop getting kinby's fixes.

## Consequences

- A workbench reads factories through a read-only mount of the hub's factories folder. A run's steps must find the files of the run's version there, not only the latest.
- A run records a version, and its run page shows the version each attempt ran.
- A change by hand in a factory's folder is still allowed. It becomes a version the next time the hub loads the factory, or keeps the last good version when the check fails.
- The hub's state now includes a git repository it writes to, which its backups must cover.
