---
name: repository-survey
description: Propose and record the setup and checks lists of repository commands the software factory runs. Use when the user asks for a repository survey, or to change either list.
---

The factory's `setup` step runs your `setup` list on the run's branch. Its `checks` step runs `setup` again, then `checks`. Each command runs in the workspace as one program and its arguments, without a shell, so a command holds no pipe, `&&`, redirect or `$VARIABLE`. An empty list is a valid one.

Asked to change one list, take the steps below for that list alone: propose and record it and leave the other as it is. Try a `checks` list after the `setup` list the coder holds.

## Process

1. **Read the repository.** Read `AGENTS.md`, the CI workflows in `.github/workflows/`, the lockfiles, `package.json` and `pyproject.toml`, whichever exist. Done when you know what CI installs and what it runs to judge a change.

2. **Draft the lists.** `setup` installs the dependencies from the lockfiles, frozen so a run never rewrites them. `checks` holds the commands CI runs to judge a pull request, and nothing `setup` already does. Done when every command CI runs on a pull request is in a list, or you can say why it is left out, such as a deploy or a step that needs a CI secret.

3. **Try each command on the default branch.** Add a temporary worktree there, detached so the default branch stays checked out where it is:

   ```sh
   git fetch origin
   git worktree add --detach /tmp/repository-survey origin/HEAD
   ```

   Run each command from it, `cd /tmp/repository-survey && <command>`, the `setup` list first, and keep each exit code and the tail of any failure. Start a command that outlasts the bash tool's timeout in the background with its output and exit code in a log file, and read the log until it ends. A command that fails because it is wrong, such as a missing script or a bad flag, gets fixed and tried again; a failure in the repository's own code stays as it is. Then remove the worktree with `git worktree remove --force /tmp/repository-survey` in a tool call of its own, and read its result before you go on. A call made in the same step as a `repository_commands_write` call waits for that approval before it runs. Done when every drafted command has an exit code and `git worktree list` shows only the workspace.

4. **Propose the lists.** Write the proposal as text in the thread before you call `repository_commands_write`, so the user reads it above the approval card: each command, its exit code, and the last lines of output of each that failed. Then list each toolchain or service the repository needs and the image lacks, such as a runtime CI installs or a database its tests reach. You change no image and start no service; those are the user's to set up. Done when every drafted command appears in the thread with its exit code.

5. **Record each list.** After the proposal, call `repository_commands_write` once for each list you proposed, with the whole list. Its approval shows the user every command, and a refusal leaves the list as it was. Done when you have told the user which lists were recorded and which were refused.
