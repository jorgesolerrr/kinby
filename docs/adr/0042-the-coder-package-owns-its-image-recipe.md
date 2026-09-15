# The coder package owns its image recipe

The software factory moves to its own repository with its tests, instance template, and coder-specific setup documentation. That repository also owns a pinned image recipe that adds coding clients and their dependencies to kinby's base image. A Python distribution alone cannot supply every executable the factory needs. The base retains generic dependencies, including Git for workspace snapshots.

The factory supports other repositories through explicit instance configuration for check commands and packaged defaults for implementation, review, and PR skills. Instance skill overrides remain available. Generic instance, plugin, routine, and hub behavior stays in kinby.

Migrating the existing coder is explicit. Preserve its edited configuration, memory, workspace, credentials, and webhook URLs, and change its routine wrappers to reference the package.

Decision agreed during [Package contract and the software factory as the first package](https://github.com/jorgesolerrr/kinby/issues/216).
