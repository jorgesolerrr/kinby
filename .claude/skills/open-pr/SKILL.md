---
name: open-pr
description: Push the ticket branch and open a pull request that closes the ticket. Use after implement-ticket, when the commit is on the branch and the checks pass.
---

Open the pull request for the branch you are on. Done when `gh pr view` shows an open PR for this branch.

1. **Check.** Run the checks the repo's coding standards list (for kinby: `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`). Fix and amend nothing: a failing check means another commit on the branch, then run the checks again.

2. **Push.** `git push -u origin <branch>`. Never push to the default branch.

3. **Open.** `gh pr create --title "<title>" --body "<body>"` with a heredoc for the body. Title: what changed, in the imperative, under 70 characters. Body, in this order:
   - `Closes #<ticket>` on the first line.
   - What changed and why, in a few sentences. Name the design decisions you took where the ticket or the standards were silent.
   - Which checks ran and their result.
   - Run the `unslop` skill over the body before posting.

4. **Report.** Reply with the PR URL. If a step fails and you cannot fix it, comment the failure on the ticket with `gh issue comment` and stop.
