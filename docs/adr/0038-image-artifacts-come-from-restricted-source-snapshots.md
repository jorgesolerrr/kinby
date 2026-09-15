# Image artifacts come from restricted source snapshots

Image preparation resolves a requested Git revision before building and exports only the runtime source files the Dockerfile is allowed to consume. Instance directories, environment files, workspaces, and subscription credentials are absent from the build context.

The artifact record includes the resolved revision, a dependency-input digest, base-image identities available from the builder, and the resulting immutable image identity. A matching artifact is reused only while that exact image still exists. Builds happen before container replacement, so a build failure cannot alter an existing image or running container.

## Consequences

- Rebuilding the same revision is not assumed to produce the same image.
- Creation and update share image preparation without sharing lifecycle orchestration.
- Package-template semantics remain separate from image preparation.
