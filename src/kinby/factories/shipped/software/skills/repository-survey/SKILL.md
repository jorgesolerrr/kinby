---
name: repository-survey
description: Propose and record the setup and checks lists of repository commands the software factory runs on a workbench. Use when the user asks for a repository survey, or to change either list.
---

The factory's `setup` step runs the workbench's `setup` list in the run's run worktree. Its `checks` step runs `setup` again, then `checks`. Each command runs as one program and its arguments, without a shell, so a command holds no pipe, `&&`, redirect or `$VARIABLE`. An empty list is a valid one.

Asked to change one list, take the steps below for that list alone: propose and record it and leave the other as it is.

You own the line on the workbench, and you reach the workbench through the hub. `list_lines` names each line's `workbench_id`. `read_workbench_file` and `list_workbench_directory` read the workbench's workspace, and `run_workbench_command` runs a command there that only reads, such as `ls`, `cat`, `grep` or `find`. The workbench refuses every other command, so you cannot try a list there: the run's `setup` and `checks` steps are the first to run it.

## Process

1. **Read the repository.** Read `AGENTS.md`, the CI workflows in `.github/workflows/`, the lockfiles, `package.json` and `pyproject.toml`, whichever exist, with those tools. Done when you know what CI installs and what it runs to judge a change.

2. **Draft the lists.** `setup` installs the dependencies from the lockfiles, frozen so a run never rewrites them. `checks` holds the commands CI runs to judge a pull request, and nothing `setup` already does. Done when every command CI runs on a pull request is in a list, or you can say why it is left out, such as a deploy or a step that needs a CI secret.

3. **Propose the lists.** Write the proposal as text in the thread before you call `record_workbench_commands`, so the user reads it above the approval card: each command, marked untried. Then list each toolchain or service the repository needs and the image lacks, such as a runtime CI installs or a database its tests reach. You change no image and start no service; those are the user's to set up. Done when every drafted command appears in the thread.

4. **Record each list.** After the proposal, call `record_workbench_commands` with the workbench's id once for each list you proposed, with the whole list. Its approval shows the user every command, and a refusal leaves the list as it was. Done when you have told the user which lists were recorded and which were refused.
