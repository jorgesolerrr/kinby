# The factory implements tickets only trusted authors wrote

The coding client reads the ticket with `gh issue view --comments`, plus its parent issue, and runs commands in the factory container. In a public repository anyone can open an issue or comment on one, and the author of an issue can edit it after the maintainer labels it `ready-for-agent`. The label alone does not keep strangers' text out of the prompt.

Before the coding client starts, the factory checks who wrote the issue, its parent issue, and every comment on either. Each one must be the repository's owner, a member or a collaborator, by GitHub's `author_association`. Text from anyone else fails the run, and the issue gets `ready-for-human`. A human removes that text and labels the issue again.

This extends ADR 0031, which trusts only the maintainer and Greptile for review feedback, to the ticket itself. Using `author_association` instead of the maintainer's login keeps the check working in an organization's repository, where the factory has no single maintainer. A comment posted after the check and before the coding client reads the ticket still reaches it. That window lasts seconds, and the factory container stays the execution boundary from ADR 0029.
