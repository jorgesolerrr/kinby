# The container entrypoint owns workspace cloning

The instance module resolves a workspace path and optional git source, but it does not clone repositories. A container entrypoint will handle clone-on-first-boot before it starts kinby. It may clone only when the configured workspace path is absent or empty, and it must leave a non-empty workspace untouched.

This keeps deployment setup out of the instance model. Local installs can populate or link a workspace without needing git behavior in kinby, while container deployments can prepare the mounted instance during startup.

## Consequences

- The current entrypoint does not clone. Operators must populate the workspace themselves until clone-on-first-boot is implemented.
- The generic image needs no workspace source baked into it.
- The instance module remains free of a git dependency and only resolves and reports workspace configuration.
