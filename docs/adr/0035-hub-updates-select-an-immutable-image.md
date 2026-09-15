# Hub updates select an immutable image

The hub initially builds images on the host from a selected kinby revision and, when applicable, a pinned package version. It records the resulting immutable image identity for each instance and reuses the image for matching build selections. Running instances keep their selected image until the user explicitly updates them. Official registry image publication is outside this first version.

The hub prepares the replacement image before stopping an instance. If preparation fails, the current instance keeps running. If the replacement fails to start, the hub reports the failure and preserves the previous image and instance data for explicit recovery. It does not automatically start an older image against data that the replacement may already have changed.

A source revision alone does not identify a built image because dependencies and base images can differ between builds. The build records its resolved inputs and resulting image identity. Selecting an existing image uses that identity rather than rebuilding the same source and assuming the result is identical.

Decision agreed during [Hub and instance lifecycle](https://github.com/jorgesolerrr/kinby/issues/215).
