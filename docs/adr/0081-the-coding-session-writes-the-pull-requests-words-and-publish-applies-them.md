# The coding session writes the pull request's words, and publish applies them

The coding client could open the pull request itself: it has the context to title it, describe it, and answer each review thread. We considered that and kept `publish` as a code step. The words and the side effects are split instead.

The coding session writes every word a reader sees into `.scratch/`: the title, the body, and each review reply. `implement`, `answer` and `fix` run in that one session per issue, so the session that wrote the change also answers its review and updates its description.

`publish` applies those files and does what has to be exact every time. It pushes with a lease. It opens the pull request with `Closes #n`, or edits the title and body on a fix round. It bases a sub-issue's pull request on its sibling's, removes `ready-for-human` from the issue, and asks the maintainer to review. It posts the replies and resolves exactly the threads that were fixed.

If the agent opened the pull request itself, each of those duties would become a prompt rule the model can skip. A hook would then have to check every one of them, so the code would only move from `publish` to the hook. A retry could also open a second pull request.

## Consequences

- The title comes from the change, not from the backlog. `publish` uses the issue's title only when the session wrote none.
- A fix round can rewrite the title and body when it changes what they say.
- The session that answers reviews keeps its context across babysitting rounds, so its context grows with each round.
