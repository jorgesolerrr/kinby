# Container contract

The root `Dockerfile` builds one image for every kinby instance. Instance identity, behavior, workspace configuration, and state do not belong in the image.

## Runtime contract

- Mount one instance directory at `/instance`. The image declares this path as a volume.
- `KINBY_INSTANCE` is set to `/instance`, so commands use the mounted instance unless an explicit path overrides it.
- Pass provider API keys and other secrets as environment variables at runtime. Do not add them to the image or `kinby.toml`.
- The image entrypoint is `kinby`. Its default command is `run`.
- Runtime data written under the instance's `.state/` directory persists with the mounted instance.

Build the image and inspect the minimal example:

```sh
docker build -t kinby .
docker run --rm \
  --mount type=bind,src="$PWD/examples/instances/minimal",dst=/instance \
  kinby instance show
```

Pass secrets with individual `--env` flags, `--env-file`, or the equivalent setting in the container platform. An env file used by the platform should live outside the image build context.

## Entrypoint and workspace cloning

The entrypoint does not clone a workspace yet. For now, an operator must populate the configured workspace path before starting the container.

When clone-on-first-boot is added, the entrypoint owns it. If `[workspace].source` is set and the configured workspace path is absent or empty, the entrypoint will clone the source there before starting kinby. It must leave a non-empty workspace untouched. The instance module only resolves and reports workspace configuration.

[ADR 0003](adr/0003-container-entrypoint-owns-workspace-cloning.md) records why this boundary belongs to the container entrypoint.
