# A package pin can carry its image recipe

A **package pin** on an instance update may carry the image recipe of the commit it pins. The hub builds the candidate with that recipe, and once the replacement is up it records the recipe with the new commit, so later updates without a pin keep building with it. A pin without a recipe keeps the recorded one, as [ADR 0056](0056-a-package-can-be-pinned-to-a-git-commit.md) said. A failed update keeps the previous recipe, the same way it keeps the previous pin.

The recipe lives in the package's repository and changes with it. Under ADR 0056 the hub recorded the recipe once, at create or adopt, and a pin moved only the commit. So when the factory added Bun to its recipe, the coder moved to the new commit with the old recipe and never got Bun. Nothing failed: the pip install succeeded, and the missing tool showed up only when a check needed it.

`kinby hub update` takes `--image-recipe <file>`, only together with `--package` and `--package-commit`. It reads the file and sends its text. The factory's CI passes the recipe file from the commit it pins, so the two always match.

The hub does not read the recipe from the package's repository itself. That would need a path convention every package follows, and a git fetch the hub does not do today.

A leaked update token can now send any recipe, which means any build step, for an instance with a git package. Before, it could only move that package to another commit of its recorded repository. We accepted the wider reach, because the token already decides what code that instance runs. The candidate is still prepared and checked before anything stops, as [ADR 0052](0052-an-update-prepares-the-candidate-then-replaces-the-container.md) says, and [ADR 0057](0057-ci-updates-instances-through-an-update-only-hub-token.md) keeps the token away from everything else.

Decision agreed during [Switch the coder's checks to bun run check](https://github.com/jorgesolerrr/kinby/issues/278), under ADR 0056 and ADR 0057.
