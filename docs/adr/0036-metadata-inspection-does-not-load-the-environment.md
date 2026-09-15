# Metadata inspection does not load the environment

Instance metadata inspection parses and validates `kinby.toml` without reading `.env` or loading plugins. Boot keeps the existing loader, which reads `.env` without overriding values already present in the process and then applies an optional model override.

Management code uses the metadata-only path. This prevents inspecting one instance from changing the environment observed while inspecting another, while preserving the established boot behavior.

## Consequences

- Manifest validation and path resolution have one implementation shared by inspection and boot.
- Callers that need runtime credentials must choose the boot loader explicitly.
- Existing interactive inspection may render turn inputs after loading an instance, but hub management never does so.
