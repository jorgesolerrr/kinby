# The hub keeps a library, and a pick is a pinned copy

Extends [ADR 0084](0084-an-instance-update-syncs-the-files-its-factorys-template-supplied.md).

An instance got its skills from its factory's template, or the agent wrote them. Nothing took a skill from a repository with a pin. We compared Hermes Agent, OpenClaw, the `skills` CLI and Claude Code's plugin marketplaces before deciding. The notes are `docs/research/hermes-skill-library.md`, `docs/research/openclaw-skill-library.md` and `docs/research/skill-install-standards.md`, each on the `research/` branch of the same name.

All four copy a skill into the agent and record a content hash. Only the skill folder is standard: the Agent Skills spec fixes `SKILL.md` and says nothing about versions, locks or registries. None of the tools shows a diff before an update. The `skills` CLI overwrites local edits, and it and Hermes never pin a commit.

## Decision

- **The hub keeps a library.** A **library entry** is a skill or a factory. It gets there in one of three ways: kinby ships it, the user writes it on the hub, or the user adds it from git.
- **An entry from git is pinned to a commit.** The user gives an address in the `skills` CLI's syntax. The hub resolves it, records the URL, path, ref and commit, and keeps the files. This is the git source shape of a Claude Code marketplace entry. Creating an instance never reads the network for a skill.
- **A pick is a copy.** It lands in the instance's `skills` folder as an instance-tier skill, with an entry in the instance's `skills-lock.json` that holds the lock's usual fields and the commit. ADRs 0013 and 0024 stand, and no new skill tier exists.
- **Nothing moves until the user moves it.** The user moves a library entry to a newer commit and sees the diff first. An **instance update** then brings that instance's picks up to the library's, by the rule of ADR 0084: a skill the instance changed is kept and listed.
- **The hub shows a skill before it lands.** It lists the files and the `SKILL.md` body, and refuses binaries and symlinks. A skill carries text and the spec's `scripts/` folder, never a tool or a hook. A request from the home instance passes the gate.

## Considered options

- **The instance fetches its own skills from git.** Rejected. Each instance would hold its own pins, and two coders would drift apart on the same skill with no place to see it.
- **Instances read the library in place.** Rejected. An instance owns its files (ADR 0064) and may edit a skill, and a library edit would change every running instance at once.
- **A scanner or a list of trusted repositories**, as Hermes and OpenClaw have. Rejected. On a single-user hub the user names every source, and a regex scan would pass a skill whose text misleads the model.
- **The `skills` CLI lock as it is.** Rejected. It records no commit, and its update never reads the installed copy.

## Consequences

- ADR 0075 retired pinned sources for packages. This is not their return: a library entry is a folder of text the hub keeps and shows, where a package was code an instance installed.
- A library can later hold MCP servers, freely picked tools and factories from git. Each is code or a new capability and needs its own trust decision.
- An instance keeps two records: what its template supplied (ADR 0084) and what it picked.
