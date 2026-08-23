# Instance discovery uses fixed precedence

Instance discovery selects the first existing instance from this order: an explicit directory or `--instance`, `KINBY_INSTANCE`, a walk from the current directory toward the filesystem root, then `~/.kinby/default/`.

A directory is an instance only when it contains `kinby.toml`. Discovery never creates one. When no rule matches, the error points the user to `kinby init`.

## Consequences

- Scripts can select an instance without depending on their working directory.
- Commands run inside an instance, including its workspace, find that instance before the home default.
- The selected instance records the matching rule for user-facing diagnostics.
