---
name: implement-ticket
description: "Implement one ticket from the repo's issue tracker: TDD at the seams the ticket names, coding standards consulted at every design decision, one commit. First of three sessions; `review-ticket` and `deepen-ticket` follow."
disable-model-invocation: true
---

Implement the ticket the user names. The ticket is the spec: fetch it per `docs/agents/issue-tracker.md` (if that file is missing, tell the user to run `/setup-matt-pocock-skills` and stop). Read `CONTEXT.md`, any ADR in the area you will touch, and the repo's coding standards (`CODING_STANDARDS.md`, `CONTRIBUTING.md`, or whatever documents how code is written here).

## Process

1. **Pin the branch.** If `HEAD` is the default branch, create a branch named after the ticket first. Done when `git branch --show-current` is not the default branch.

2. **Build test-first.** Use the `tdd` skill at the seams the ticket names. A ticket that names the public interface counts as seam confirmation; ask the user only when the ticket names no seam or the seam is ambiguous. Every **design decision** (where a seam goes, how an interface is shaped, what a thing is called, which pattern to reach for) is settled by the coding standards; when they are silent, follow the nearest existing code and note the choice in the commit message. Run typechecking and the touched test files as you go; run the full suite once the ticket's behavior is in. Done when every requirement in the ticket has a passing test at a seam and the full suite is green.

3. **Commit.** One commit on the branch, message referencing the ticket (`#123`) so the next session can find it. Done when `git status` is clean; reply to the user with the branch, the commit, and that `/review-ticket` is the next session.
