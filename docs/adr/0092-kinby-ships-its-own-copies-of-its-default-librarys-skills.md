# kinby ships its own copies of its default library's skills

The software factory's coder carried eight skills copied from `jorgesolerrr/skills`. `skills-lock.coder.json` pinned them, `bun run skills:sync` refreshed them, and a check failed when one was edited here. With a **library** on the hub ([ADR 0091](0091-the-hub-keeps-a-library-and-a-pick-is-a-pinned-copy.md)), those skills become the default library.

kinby ships them as its own text. They are copied into this repository once and edited here from then on, so they stop following the registry. `skills-lock.coder.json`, the coder half of `skills:sync` and its drift check retire. `skills-lock.json` stays, since it pins this repository's own `.claude/skills`.

A hub receives the shipped entries as it receives the shipped `software` factory: copied onto the hub, where the user may edit or delete one. A kinby upgrade offers a newer shipped version as a library update with its diff, and never overwrites an entry the user edited. The shipped library is not the kinby skill tier, and none of it reaches an instance until picked.

## Considered options

- **Ship addresses, not text**: the `coder` role lists suggested picks as git addresses, and the hub fetches them at creation. Rejected. A fresh hub would need the network and a third repository to make a working coder, and the skills kinby uses will differ from the registry's.
- **Keep following the registry.** Rejected for the same reason: the copies need to differ.

## Consequences

- kinby redistributes and edits this text, so each shipped skill keeps its credits and licence in its own folder. A skill whose licence forbids a modified copy stays out. [#646](https://github.com/jorgesolerrr/kinby/issues/646) audits them before the shipped library is built.
- The `coder` role names all eight as **suggested picks**. `implement-ticket` stays in the role's template, because the factory's own process needs it.
