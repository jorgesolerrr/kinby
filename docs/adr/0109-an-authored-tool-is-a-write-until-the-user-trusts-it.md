# An authored tool is a write until the user trusts it

Settles what [ADR 0028](0028-instance-tools-are-gated-core-write-tools-over-whole-files.md) deferred.

Ticket [#161](https://github.com/jorgesolerrr/kinby/issues/161): the agent can write its own routines and skills, but not tools, because a tool declares its own `write` flag, and a model-written tool marked `write=False` that writes anyway gets past the gate. The research in [#918](https://github.com/jorgesolerrr/kinby/issues/918) and [#919](https://github.com/jorgesolerrr/kinby/issues/919) found that Hermes, OpenClaw, Claude Code, OpenHands and Goose keep agent-written code as files the agent runs, never as tools it registers. Letta lets the agent write tools, without a sandbox. None of them answers the self-declared flag. kinby gives each instance **instance code** and lets the agent expose **authored tools** from it.

## Decision

- **Each instance keeps one Python project, its instance code, at `/instance/code/`.** It is managed by `uv`, with its own `pyproject.toml`, lockfile and venv, and holds the agent's scripts, the packages they need, and the tools it authored. An update or a recreated container rebuilds the venv from the lockfile. The user's `tools/` is not part of it.
- **The agent changes instance code only through instance tools.** `code_write(path, content)`, `code_delete(path)` and `code_add_package(name)` are ordinary writes under the gate (ADR 0028), each recorded as a config change. `code_run(script, args)` runs a script in the venv, with bash's timeout and output cap, and with variables whose names look secret (KEY, TOKEN, SECRET, PASSWORD) stripped from its environment unless the instance code declares that it uses them. Its secrets come from the instance's `.env`, which hub secrets fill; the agent never sets one, and names a missing one so the instance shows pending setup.
- **An authored tool is a function in instance code that the agent exposes.** `code_expose_tool(name)` registers it, and the tool registry loads it at the next turn start, as it loads `tools/*.py` (ADR 0010). Before registering, kinby parses the module and imports it in a child process inside the venv to check that the tool's shape loads. A tool that still fails at turn start leaves the registry's last valid set in place.
- **The gate treats every call to an authored tool as a write, whatever it declares.** It stays a write until the user marks it read-only from an approval or the web app. That marking is a config change the user made. Writing or exposing a tool asks under the ordinary gate, not in every mode. The approval shows the tool's name, its declared flag and its source.
- **The agent changes or removes only the tools it authored**, going by the config change history. Tools in `tools/` and picked tools are read-only to it.
- **An authored tool may be a routine's code step**, under the same rule: a write until the user marks it read-only.

## Considered options

- **The agent writes into `tools/`, which every write asks for in every mode, `full-access` included.** Rejected. It mixes the agent's tools with the user's and needs a new gate behaviour. Once every call to an authored tool is a write, writing one is no escalation: `code_run` and bash already run arbitrary code in the same container, which the sandbox contains.
- **Trust the declared flag.** Rejected. It is the bypass this ticket was opened for.
- **Always a write, with no way to mark one read-only.** Rejected. A harmless lookup tool would ask on every call under `ask` forever.
- **Only a static parse before registering.** Rejected. It misses the failures that matter (imports, decorators), and importing in a child process adds no risk the agent does not already have.
- **Scripts through bash only, with no instance tools.** Rejected. It leaves no record of what the agent added, and a `code_run` approval reads better than a raw shell line.

## Consequences

- An instance gains `/instance/code/` and five instance tools. A config change gains the marking that trusts an authored tool.
- The home instance cannot write another instance's code, and authored tools cannot be shared through the library, in v1. Both are deferred.
