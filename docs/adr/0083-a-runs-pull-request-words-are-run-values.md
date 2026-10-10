# A run's pull request words are run values

Amends [ADR 0081](0081-the-coding-session-writes-the-pull-requests-words-and-publish-applies-them.md).

The coding session writes the pull request's title and body to two fixed files in `.scratch/`, and `publish` read them from there. Runs share one workspace, and their steps interleave: one run implements while another takes a fix round. A fix round on #598's pull request rewrote the body file between #601's `implement` and its `publish`. #601's pull request opened with #598's body, `Closes #598` included, and #598's own pull request never got its update.

The hook of each client step now reads the two files when the step ends, removes them, and records them as the run's values `title` and `body`. `publish` takes the values and reads no file. This is what `check_answers` already did with the review replies, and it is ADR 0076's rule: a step's result comes from its hook. An instance takes one step at a time, so no other run's step can come between a session writing its words and the hook taking them.

We considered a file path named for the run. The prompts would then need the run's id, and `prepare`'s clean of the workspace would have to spare other runs' files.

A fix round's `answer` records the title or body it did not rewrite as empty. Without that, the run would still hold the words `implement` recorded, and `publish` would apply them again over what an earlier round rewrote.

## Consequences

- The hook removes the files even when the step's checks fail, so a failed step's words never become another run's. A retry has to write them again.
- The title and body are in the run's data, so every later client step of the run reads them in its prompt.
- `fix` comes after `answer` (ADR 0080), so a check repair keeps the words `answer` recorded, and overrides them only when it writes its own.
