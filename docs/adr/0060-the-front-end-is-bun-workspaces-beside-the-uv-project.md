# The front-end is Bun workspaces beside the uv project

The repository is a monorepo with two toolchains side by side. Python stays where it was: `pyproject.toml`, `src/kinby`, and `tests/` at the root, run by uv. The TypeScript side is a set of Bun workspaces declared in a root `package.json`: `apps/web` is the web app, and `packages/contract` holds the contract's generated types and the socket client that any client runs. Bun is the package manager and script runner only. Vite builds, Vitest tests, and Node is never required. The root `bun run check` is the one gate before a commit: it runs the four uv checks, then each workspace's check.

This replaces the charting note on [kinby next](https://github.com/jorgesolerrr/kinby/issues/211) that put one npm project in `web/`, and the npm, `justfile`, and `src/kinby/web/dist` parts of the [front-end stack research](https://github.com/jorgesolerrr/kinby/issues/214).

## Considered Options

- **One `web/` project.** With only one JavaScript package, workspaces add nothing, and code that runs in any client (types, the socket client) ends up mixed in with UI code.
- **Everything as a workspace member, Python under `packages/`.** A directory-tree change that rewrites imports, the Dockerfile, CI, evals, and the coder's check paths for no behaviour.
- **A `justfile` as the check entry point.** The root scripts do the same job without another tool on the developer machine, in CI, and in the coder image.

## Consequences

- The web app is built into `apps/web/dist` and copied into the hub image by a Bun build stage. It is not shipped in the Python wheel, and instances never serve it.
- The coder can work on the TypeScript side only once its image carries Bun and its configured checks run `bun run check`.
