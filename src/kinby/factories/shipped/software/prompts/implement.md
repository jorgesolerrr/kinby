Implement the GitHub issue numbered `work_item.issue` in the factory run's data. The factory checked out the branch `results.branch` for it, from `results.base`. If the workspace is on another branch, switch to `results.branch` before you start.

Follow the implement-ticket skill. A good result:

- Does what the ticket asks and stays in its scope. The pull request body names what you skipped.
- Is the smallest complete change, as the ponytail skill describes.
- Lands each new behavior test-first, with the tdd skill. A change with no new behavior, such as config, docs, a rename or wiring, needs no new test, and the checks still pass.
- Leaves comments only for a why the code cannot say. The no-comments skill holds the reasons.
- Names things with the terms in `CONTEXT.md`. An ADR records only a decision that is hard to reverse.
- Is one commit on the branch, its message referencing the issue as `#<number>`, and `bun run check` passes.
- Has its pull request title in .scratch/pr-title.txt and its body in .scratch/pr-body.md, written with the pr skill and then run through unslop.

Shape interfaces and seams with the codebase-design and api-and-interface-design skills.

If the ticket is ambiguous or contradicts the code, make no commit. End with one paragraph that says what is unclear; the maintainer reads it on the issue.

The factory pushes the branch and opens the pull request.

Read a skill from /instance/skills/<name>/SKILL.md. Paths in a skill are relative to its own directory.
