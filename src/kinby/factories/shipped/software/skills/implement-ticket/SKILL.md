---
name: implement-ticket
description: Implement one GitHub issue test-first and commit it on the branch the software factory checked out. The factory's implementation step.
---

Implement the ticket the prompt names. The ticket is the spec: read it with `gh issue view <number> --comments`, including any parent issue it links. Then read how this repository wants code written: its agent instructions (`AGENTS.md`, `CLAUDE.md`), contributing guide, coding standards, domain glossary (`CONTEXT.md`), and any decision record in the area you will touch, whichever of these exist.

## Process

1. **Stay on the branch.** The factory checked out a fresh branch for this ticket from its base. Do not switch branches, push, or open a pull request. The factory owns those.

2. **Build test-first.** Write each behavior's test before its code, at the seams the ticket names. When the skills catalogue holds a test-first skill, follow it. A ticket that names the public interface counts as seam confirmation; when it names no seam, pick the narrowest public interface that shows the behavior and say so in the commit message. Every design decision (where a seam goes, how an interface is shaped, what a thing is called, which pattern to reach for) is settled by the repository's standards; when they are silent, follow the nearest existing code and note the choice in the commit message. As you go, run the tests that cover what you change and the repository's fast checks, such as lint, format and type checking. The factory runs the repository's checks after you commit, so leave the rest to it. A change with no new behavior, such as config, docs, a rename or wiring, needs no new test. Done when each new behavior the ticket asks for has a passing test at a seam, and the tests that cover the change and the fast checks pass.

3. **Commit.** One commit on the branch, its message referencing the ticket (`#123`). Done when `git status` shows nothing to commit.
