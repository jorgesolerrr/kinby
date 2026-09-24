# Adoption can record the instance's package

`instance.adopt` and its preview take an optional **package** selection: the package ID, distribution, git commit or index version, and image recipe. Adoption records it as the instance's package. It does not rebuild anything. The container the hub takes over keeps the image it was running, and the next update builds the recorded package into its candidate image ([ADR 0052](0052-an-update-prepares-the-candidate-then-replaces-the-container.md)). From then on a package pin moves the commit the way it does for a created instance ([ADR 0056](0056-a-package-can-be-pinned-to-a-git-commit.md)).

Without this, an adopted instance had no package in the registry. An update never switches packages, so a pin on it was refused, and an update without one built a vanilla image. The existing coder could not move to `kinby-code-factory` through the hub at all. We found this while migrating the factory ([kinby-code-factory#2](https://github.com/jorgesolerrr/kinby-code-factory/issues/2)).

The selection has to agree with the manifest's `[package]` table, which the operator writes when migrating the instance. The preview reports a blocking `package-mismatch` finding when one names a package and the other does not, or when their IDs or distributions differ. Versions are not compared: a git commit names no version before it is built, and the manifest's version is the one its copied configuration came from ([ADR 0039](0039-package-updates-preserve-instance-configuration.md)).

The hub installs a package with `uv pip install --no-sources`. The factory's own `[tool.uv.sources]` points `kinby` at git `main` for development, and without the flag that source would replace the kinby already in the image.

`kinby hub adopt --connect <url> <path> <container>` previews with `--preview` and adopts otherwise, following the handoff the way `kinby hub update` follows an update. It reads the access token from `KINBY_TOKEN`. `--package <file>` takes the selection as JSON.

Decision agreed during [Migrate the software factory into kinby-code-factory](https://github.com/jorgesolerrr/kinby-code-factory/issues/2), under [ADR 0052](0052-adoption-previews-first-then-claims-ownership-in-one-step.md) and [ADR 0056](0056-a-package-can-be-pinned-to-a-git-commit.md).
