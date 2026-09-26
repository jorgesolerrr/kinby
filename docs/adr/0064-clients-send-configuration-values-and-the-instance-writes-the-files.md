# Clients send configuration values and the instance writes the files

Ticket #222 settled how the web app edits a running instance. The app never edits TOML or YAML as text. For permissions, the manifest, and the package config, the app sends typed values from controls that offer only valid choices. The instance validates them and writes the file itself. The behavior prompt and the recap prompt are prose, so the app edits those two as text. Nobody should have to get a file format right to change a setting.

## Considered options

- **Whole-file editors validated by the real loaders.** Rejected after the prototype. The user has to get the syntax right and only learns it's wrong from a failed save.
- **The app serializes the file itself.** Rejected. The format would have two writers, and the instance would parse whatever a client produced.

## Consequences

- The instance needs a TOML writer that keeps the user's comments and formatting. Its absence is why ADR 0028 ruled out `set_model`. The agent still has no model tool; this changes only the client path.
- A package declares a schema for its package config, and the app renders the form from it. The package's validator still has the last word.
- Every write carries the hash of what the client read. The instance refuses it if the file changed since, because the agent's instance tools and the routine failure policy write the same files.
- Hand edits on disk still work. The instance reads the files as before.
