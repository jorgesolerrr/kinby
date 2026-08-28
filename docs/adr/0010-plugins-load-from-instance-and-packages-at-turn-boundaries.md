# Plugins load from the instance directory and installed packages, at turn boundaries

Tools are plain Python functions decorated with `kinby.plugins.tool(write=...)`; skills are `skills/<name>/SKILL.md` files with `name` and `description` frontmatter. The core discovers tools in the instance `tools/` directory and in the `kinby.tools` entry-point group (the default package is kinby's own entry point, switchable off in the manifest), and rescans both at the start of every turn by file signature. No file watcher, no reload command, no MCP client in v1.

Rescanning at turn boundaries keeps the same rhythm as the manifest re-read, avoids a background task and a dependency, and matches the prompt-cache reality: a tool-list change costs one cache write per turn anyway, so applying it mid-turn buys nothing. A rescan that fails (syntax error, two files exporting one name) keeps the previous set and reports a warning; a deleted file removes its tools at the next turn. An instance file shadows a same-named entry-point tool so a user can replace `bash` without forking kinby.

MCP stays out of v1: native plugging covers the destination, and a second tool source before the first has evals is machinery without a user. It returns as an external-server tool source once native tools are proven.
