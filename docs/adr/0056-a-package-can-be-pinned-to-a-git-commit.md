# A package can be pinned to a git commit

A **package** selection names either an index version or a git commit: the repository URL and a full 40-character SHA. The image preparer installs a commit with `uv pip install git+<url>@<sha>` on top of kinby's base image. The package depends on `kinby` without a pin, so the kinby already in the image satisfies it and the install never replaces it. Each commit is its own input to the image artifact, so two commits never share a cached image. A short SHA, a branch, or a tag is refused, because each of them can name a different commit tomorrow.

An **instance update** may carry a package pin: the package ID and a new commit SHA. The hub keeps the repository URL, distribution, and image recipe the instance already records and moves only the commit. The pin has to name the instance's current package ([ADR 0040](0040-an-instance-starts-from-at-most-one-package.md)), and that package has to come from git already. An index version has no repository to move within. Without a pin, an update carries the recorded selection along unchanged ([ADR 0052](0052-an-update-prepares-the-candidate-then-replaces-the-container.md)).

The registry records the new pin once the replacement is up: after it answers its lifecycle endpoint, or after its container exists for an instance that stays stopped. A failed update keeps the previous pin. The image selection still moves when the container is created, as ADR 0052 says, so after a failed update the two can disagree. A later update without a pin then goes back to the recorded commit. That is the point of a failed update: the recorded pin is the last one that ran.

A commit pin checks the prepared package's ID and distribution, not its version. A commit names no version before it is built, and the image reports whatever version the commit declares.

Decision agreed during [Roll out instance updates from CI through the hub](https://github.com/jorgesolerrr/kinby/issues/269), under [ADR 0035](0035-hub-updates-select-an-immutable-image.md) and the [factory migration spec](https://github.com/jorgesolerrr/kinby-code-factory/issues/1).
