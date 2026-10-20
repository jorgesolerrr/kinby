Implement the GitHub issue numbered `work_item.issue` in the factory run's data. The factory checked out the branch `results.branch` for it, from `results.base`, and the workspace is on it.

Follow the implement-ticket skill. A good result:

- Does what the ticket asks and stays in its scope. The pull request body names what you skipped.
- Is the smallest complete change.
- Lands each new behavior test-first. A change with no new behavior, such as config, docs, a rename or wiring, needs no new test.
- Leaves comments only for a why the code cannot say.
- Names things with the terms in the repository's domain glossary, when it has one. An ADR records only a decision that is hard to reverse.
- Passes the tests that cover what it changed and the repository's fast checks, such as lint, format and type checking. The factory runs the repository's checks after the commit.
- Is one commit on the branch, its message referencing the issue as `#<number>`.
- Has its pull request title in .scratch/pr-title.txt and its body in .scratch/pr-body.md.

If the ticket is ambiguous or contradicts the code, make no commit. End with one paragraph that says what is unclear; the maintainer reads it on the issue.

The factory pushes the branch and opens the pull request.
