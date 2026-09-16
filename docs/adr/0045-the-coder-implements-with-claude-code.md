# The coder implements with Claude Code

The maintainer selected Opus 5 for implementation. The delegated pipeline now selects
a coding client explicitly and defaults to Claude Code with `claude-opus-5`. Changing
the model name alone cannot select a client because Codex and Claude Code have
different command, result, and resume protocols.

Implementation and check repair use the same client session. Claude Code uses its
subscription login with `ANTHROPIC_API_KEY` removed from the subprocess environment.
Its JSON result supplies the session ID, outcome, and token usage. Total input tokens
include uncached, cache-read, and cache-creation tokens. The pipeline report names
the run `implementation` instead of `codex` and records the client; historical reports
remain unchanged. Explicit Codex selection remains available.
