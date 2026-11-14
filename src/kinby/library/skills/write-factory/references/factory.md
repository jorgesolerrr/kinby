# The factory reference

Every `factory.yaml` field, what each step kind does, where a run goes next, and the code a factory's folder holds. The worked example at the end passes the factory check as written.

A factory runs on a workbench: the place with the image, the secrets, the workspace and its lists of repository commands. Set up on one workbench under an owning instance, it is a line. Each run of the line works on that workbench, in a run folder kinby cuts for it.

## factory.yaml

- `name`: the factory's name, the same as its folder's. Lowercase letters, digits and `-`.
- `requires`: what a workbench must provide for the steps to run on it. See Requires.
- `intake`: what fires the line's intake and the tool that finds work items. See Intake.
- `work_item`: the values each work item carries, by name and type: `str`, `int` or `bool`. A name is lowercase letters, digits and `_`.
- `steps`: the sequence of steps, run in order. At least one. See Steps.
- `done_requires`: values the run must hold once its last step ends. A value missing or false fails that step.
- `needs_human`: a tool the hub calls each time a run stops for the user. See Needs human.
- `ended`: a tool the hub calls once when a run ends done or cancelled. See Ended.
- `closing`: the signals that end a run that needs the user, once its work finished elsewhere. See Closing.
- `worktree`: a tool that names the branch of each run's run worktree and the base it is cut from. See The run folder.

## Requires

Each field may be left out.

- `image`: one of the image recipes kinby ships. Left out, kinby's base image. Setting up a line refuses a workbench built from another recipe, so a factory that leaves it out runs only on a workbench on the base image. kinby ships one:
  - `coder`: the base image with `gh`, Claude Code, Codex, Bun, Node and pnpm, for the coding client engines.
- `secrets`: the secrets the steps read, as environment variables of the workbench. See Fields.
- `values`: named values that are not secrets, such as a site's address, which the workbench's record holds by name. See Fields.
- `commands`: the names of the workbench's lists of repository commands the steps run, such as `setup` and `checks`. A `command` step's `{ commands: <name> }` names one of them. The workbench holds the commands themselves, so the factory names no repository's commands.
- `logins`: the sign-ins the workbench runs, which it keeps through a recreation. Setting up a line refuses a workbench whose login of that `id` has not signed in. See Logins.
- `browser`: `true` when the steps need a browser. Setting up a line refuses a workbench created without one. Its browser session stays between runs, so a site a step signed in to is still signed in at the next run.
- `suggested_picks`: skills of kinby's shipped library the factory suggests the workbench picks, by name. Setting up a line offers them, and the user may decline them.

### Fields

Each entry of `secrets` and `values`:

- `name`: a secret's name is an environment variable name, such as `GH_TOKEN`.
- `label` and `description`: what the user sees when they set the line up. Neither may be empty.
- `type`: `text`, `multiline`, `boolean`, `integer`, `choice`, `email` or `url`. A secret is `text` or `multiline`.
- `required`: whether setup needs a value.
- `default`: the value when the user sends none, of the field's type. A secret never has one.
- `choices`: the values a `choice` field offers. Only a `choice` field has them.

### Logins

Each entry of `logins` is a sign-in the user runs on the workbench, in a setup container:

- `id`: names the login's volume, so letters, digits, `.`, `_` and `-`, starting with a letter or digit.
- `label` and `description`: what the user sees.
- `command`: what the setup container runs, as a list of arguments.
- `volume`: the absolute path the login's volume mounts at, on the workbench and in the setup container.
- `prompt_pattern`: a regular expression with the named groups `url` and `code`, which finds the sign-in URL and the one-time code in the command's output.

## Intake

Optional. Without it, a line's runs start only when its owner hands a work item in. Setting up a line creates the line's intake routine in its owner from it, and removing the line deletes the routine.

- `call`: a tool of the factory's folder, run on the workbench. Its `signal` parameter receives the delivery that fired it, or `{}`. It returns a list of work items, each a mapping of the `work_item` values, or `None` for none, and the hub hands each to the line. A work item that matches an unfinished run of the line returns that run, so finding the same item again starts nothing.
- `schedule`: a cron expression with five fields, as a routine's.
- `delivery`: deliveries fire it at `/lines/<line_id>/signals` on the hub, one address per line that a change of owner keeps.
  - `secret`: the owner's secret the deliveries are signed with. Setting up the line, or giving it another owner, needs that owner to hold it.
  - `auth`: `token`, the default, or `hmac-sha256`. `signature_header` and `delivery_header` are as for a routine's signal.
- It takes a `schedule`, a `delivery` or both.

## Steps

Every step runs on the workbench, in the run folder. Every step has these fields:

- `id`: unique in the factory. Lowercase letters, digits, `_` and `-`.
- `kind`: `agent`, `command`, `code`, `wait` or `approve`.
- `requires`: values the step needs, from the work item or an earlier step's `results`. The check refuses a value neither holds. A value false counts as missing.
- `results`: the values the step hands on, by name and type, as in `work_item`.
- `outcomes`: what each outcome a check or code step names does next. See What happens next.
- `retry`: how many times a failed attempt runs again.
- `failed`: `{ retry: <n> }`, how many times a failed attempt runs again, or `{ back: <step>, max: <n> }`, the earlier step it is sent back to instead. Either way the next attempt reads what failed as the value `failure`.
- `timeout`: a duration such as `90s`, `60m`, `2h` or `7d`.

An `agent`, `command` or `code` step may name a `check`: a hook of the factory's folder that records the step's values and names its outcome, or fails the step. See Checks. A step without one records only what its kind records itself.

Each kind adds its own:

- `agent`: a model doing the work from a prompt.
  - `engine`: what runs it. `claude` and `codex` are coding clients. `kinby` is a kinby turn.
  - `prompt`: a file of the factory's folder, such as `prompts/implement.md`. The message the engine gets is the prompt followed by the work item and the values the run holds, `failure` and `notes` among them, as data.
  - `model`: for a coding client, the client's model, such as `claude-opus-5-5`, and left out, the line's model when the engine runs its provider's models, `anthropic` for `claude` and `openai` for `codex`, else the client's default. For `kinby`, a `provider:model` such as `anthropic:claude-opus-5-5`, and left out, the line's model. The workbench must hold that provider's `<PROVIDER>_API_KEY`: setting up the line asks for it when the workbench lacks it.
  - `effort`: `low`, `medium`, `high`, `xhigh` or `max`. Codex has no `max`. Left out, the client's default. The `kinby` engine ignores it.
  - `resume`: an earlier `agent` step on the same coding client, whose session this one continues, so one session can write a change, answer its reviews and repair its checks. The `kinby` engine keeps no session, and the check refuses `resume` on it.
  - Both engines are offered the skills of the factory's folder, as `skills/<name>/SKILL.md`, and the workbench's picks. Each attempt's transcript is kept on the workbench, and the run shows it.
  - A coding client runs without kinby's gate: Claude Code edits and runs commands unasked, Codex runs without approvals or sandbox. Without `timeout` an attempt stops after 60 minutes.
  - A kinby turn has bash and the file tools, in the run folder only, and `view_write`, whose view the hub writes among the views of the line's owner and names in the run's attempt. On a workbench with a browser it has the browser tools too. It goes through kinby's default gate policy: when the gate asks about a call, the run parks and the user gets a notification that names it, and their answer resumes the turn. `browser_takeover` parks the run the same way until the user finishes the takeover. The `kinby` engine ignores `timeout`: the line's budget bounds the turn.
- `command`: commands in the run folder, each without a shell.
  - `run`: the commands, in order. A `{{name}}` in a command stands for that run value, as one argument. An entry `{ commands: <name> }` runs the workbench's list of repository commands of that name, which `requires.commands` must declare. Exit code zero passes. The first that fails fails the step, with the command and the last 2,000 characters of its output as the attempt's summary.
  - No gate. Without `timeout` the commands run as long as they take.
- `code`: a tool of the factory's folder the model never sees.
  - `call`: the tool's name. Its parameters named like a run value receive it, and `run_id` the run's id. A mapping it returns is the step's values, and a `HookResult` also names an outcome. Raising fails the step.
  - No gate: the tool runs even when it writes.
- `wait`: parks the run, holding no workbench.
  - `signal`: filters, at least one. A signal matches a filter when it carries every field of the filter with the same value and type. A field reads `routine`, the routine the signal came in on, `headers.<Name>` or `body.<key>.<key>`. A value `"{{name}}"` stands for that run value. A signal any routine of the line's owner accepts can match, the intake's deliveries among them. A match moves on and records nothing.
  - `quiet`: a duration. With no signal for that long, the wait moves on by itself and records `quiet` as true.
  - `deadline`: a duration. With no signal for that long, the wait fails.
- `approve`: parks the run until the user approves it or sends it back, holding no workbench.
  - `summary`: what the user is asked to approve. A `{{name}}` stands for that run value, which the work item or an earlier step must declare, so the summary can name the draft to read.
  - The hub notifies the user when the run parks here, so no step exists only to tell them.
  - Approving moves on and records nothing. Sending back goes to any earlier step, with notes the steps from there read as the value `notes`, so no step saves them first.

## What happens next

- An attempt that ends clean and names no outcome goes to the next step. After the last step, the run is done once `done_requires` holds.
- `outcomes` maps a named outcome to `next`, `done`, `stop` or `{ back: <step>, max: <n> }`.
  - `next`: the next step. On the last step it is `done`.
  - `done`: the run is done once `done_requires` holds.
  - `stop`: the run ends cancelled, without `done_requires`.
  - An outcome the step doesn't map fails the attempt.

### Retries

- Without `retry` or `failed`, an `agent` step runs once more after a failure, and other kinds never do. `retry: 0` never runs again.
- `failed: { retry: <n> }` runs the step again up to `n` times, and records what failed, the attempt's summary, as `failure`, so a redraft reads why its draft failed. A plain `retry` records nothing.
- The count is per step over the whole run, until the user retries or sends the run back.
- A timed-out attempt never runs again and is never sent back.
- A coding client that hits its subscription limit parks the run until the plan window resets, then runs again without using a retry.
- A step declares `retry` or `failed`, never both.

### Send-back

`{ back: <step>, max: <n> }`, in `outcomes` or `failed`, sends the work to an earlier step:

- `back`: the step, which must come earlier. `max`: how many times this edge may send work back, at least 1. One more makes the run need the user.
- The run drops every value the target step and the steps after it recorded, except the sending step's own, which travel back with the work.
- Under `failed`, it records what failed, the attempt's summary, as `failure`.
- Sent back to the first step, the run gets a fresh run folder, cut again from the base.

### Needs human

A run needs the user when a failure has no retry left, an attempt times out, a send-back reaches its `max`, or the factory or the step no longer exists. A step that cannot start fails, such as one that runs a list of repository commands the workbench lacks, or an agent step once the line's daily budget is spent. A value a later step needs and no step recorded fails the step that should have recorded it. The run folder stays as it is, with its uncommitted work.

The hub then notifies the user and tells the line's owner. The user can retry the step, send the run back to an earlier step, with notes read as `notes`, or cancel it. Handing the same work item in again also retries it.

If `needs_human` is set, the hub calls its `call`, a tool of the factory's folder, on the workbench. Its parameters named like a run value receive it, `run_id` the run's id, `step` the step the run stopped at, and `summary` that attempt's summary. A failed call is tried again until the run moves on.

### Ended

If `ended` is set, the hub calls its `call` once a run ends done or cancelled, by any path, before it removes the run folder. Its parameters receive the run values and `run_id`, never `step` or `summary`. A failed call is tried again.

### Closing

`closing` ends a run that needs the user:

- `done`: filters. A signal matching one ends the run done, without `done_requires`.
- `cancelled`: filters. A signal matching one ends the run cancelled.

A filter reads as a `wait`'s does.

## Checks

A check is a function in the factory's folder's `hooks/`, decorated with `hook`. Its name is the function's, and a step names it as its `check`. `hook`, `StepEnd` and `HookResult` come from `kinby.plugins.hooks`. kinby's default hooks are no check. The worked example has two.

- It takes a `StepEnd`, with these fields:
  - `workspace`: the `Path` of the run folder.
  - `ending`: how the step ended, a `StepEnding` from `kinby.contracts`. See Step endings.
  - `work_item`: the work item's values, by name.
  - `results`: every value the run's earlier steps recorded, by name.
  - `instance`: None on a workbench.
- It returns a `HookResult`, or None to record nothing. Its fields, each with a default:
  - `values`: the values the step records, by name, as its `results` declare them. Each is a `str`, `int` or `bool`.
  - `outcome`: the outcome it names, one of the step's `outcomes`. None takes the next step.
  - `summary`: only a `code` step's tool sets it. A check's step keeps the summary it ended with.
- Raising fails a step that ended `clean`, and keeps any other ending.
- It runs for every ending of the step but `asked`, a failed one included. It may be `async def`. A plain `def` runs in a thread.
- A step's result comes from the world, never from what the agent says it did. A check reads the run folder, the repository or an outside system, and records what it finds.

### Step endings

`StepEnding` is a string enum. A check compares `end.ending` with its members or their values:

- `clean`: the step did its work. The outcome says what the run does next.
- `failed`: the step failed. The run tries it again under its `retry` or `failed`.
- `timed-out`: the step ran past its `timeout`. The run never tries it again.
- `interrupted`: the hub stopped while the attempt ran. Only old runs hold it, and it counts as failed.
- `needs-human`: the step cannot run until the user supplies what it lacks. The run stops for them.
- `limited`: the subscription's limit stopped the step's coding client. The run waits for the reset and tries again without using a retry.
- `asked`: the gate asked about a call of the step's kinby turn, or the turn asked the user to take over its browser. The run parks until the user answers or finishes the takeover, which resumes the turn without using a retry.

## Tools

A tool is a function in the factory's folder's `tools/`, decorated with `tool` from `kinby.plugins.tools`. Its name is the function's, its description the docstring, and its parameters, with their type hints, are its arguments. It may be `async def`. A `code` step, `intake`, `needs_human`, `ended` or `worktree` calls it, on the workbench, where the workbench's secrets are its environment variables.

A tool runs outside the run folder. A parameter named `workspace` receives the path of the workbench's workspace, its clone or plain folder, and one named `folder` the path of the run folder, for a call made in one: a `code` step's, `needs_human`'s or `ended`'s. Either may be typed `Path`.

The decorator's arguments, all by keyword:

- `write`: required. True for a tool that changes anything.
- `paths`: the names of the parameters that hold a path, which a `code` step resolves against the run folder before the call.
- `hub`, `notify` and `view`: these mark kinby's own hub, notify and view tools. A factory's tool leaves them out.

A workbench has no tool context, so a tool that takes a `ToolContext` parameter fails.

## The factory's folder

- `factory.yaml` declares the factory.
- `prompts/`: the agent steps' prompts, at any path in the folder.
- `hooks/`: the checks.
- `tools/`: the tools.
- `skills/`: skills of the factory itself, such as one that says how to implement a ticket, offered to its agent steps beside the workbench's picks.

Nothing copies the folder into a workbench: each step reads it as it is then, so an edit reaches every line at its next step. The factory names no repository's commands or conventions, and its prompts name no skill a workbench picks. Each workbench holds those itself, in its lists of repository commands and its picks.

## The run folder

kinby cuts a folder for each run when its first step starts: a run worktree when the workspace is a git clone, and a directory named for the run otherwise. Every step runs in it. A stop for the user, a retry and a park leave it as it is. It goes when the run ends done or cancelled, after the `ended` call. No step cuts or removes it.

Without `worktree`, the run worktree is on a branch kinby names for the factory, the work item and the run, cut from origin's default branch. With it, kinby first calls `worktree`'s `call` in the workspace, with the run values and `workspace` as a tool's parameters receive them. It returns the `branch` and the `base`, a branch of origin the run worktree is cut from, and the run holds both as values. A call that raises, or returns no branch or base, fails the step before it starts. Sent back to its first step, the run calls it again and starts over from the new base.

## A worked example

A factory that implements issues of a repository: it finds issues labeled `ready-for-agent`, has Claude Code implement one, runs the repository's checks and sends a failure back, has a kinby turn title the change, and asks the user before it opens the pull request. The kinby turn runs on the line's model, so setting the line up asks for that provider's key. A tool runs outside the run folder, so `gh` finds the repository in `GH_REPO`.

`factory.yaml`:

```yaml
name: tickets
requires:
  image: coder
  secrets:
    - name: GH_TOKEN
      label: GitHub token
      description: A token with repo scope. gh and git push run as it.
      type: text
      required: true
    - name: GH_REPO
      label: Repository
      description: The repository gh works on, as owner/name.
      type: text
      required: true
    - name: CLAUDE_CODE_OAUTH_TOKEN
      label: Claude Code token
      description: Claude Code's subscription token, from `claude setup-token`.
      type: text
      required: true
  commands: [setup, checks]
intake:
  call: find_issues
  schedule: "*/30 * * * *"
work_item: { issue: int }
steps:
  - id: setup
    kind: command
    run: [{ commands: setup }]
  - id: implement
    kind: agent
    engine: claude
    model: claude-opus-5-5
    effort: high
    prompt: prompts/implement.md
    check: record_commit
    results: { commit: str }
    failed: { retry: 2 }
  - id: checks
    kind: command
    run: [{ commands: checks }]
    failed: { back: implement, max: 2 }
  - id: title
    kind: agent
    engine: kinby
    prompt: prompts/title.md
    check: record_title
    results: { title: str }
  - id: review
    kind: approve
    summary: "Open the pull request {{title}} for issue {{issue}}."
  - id: publish
    kind: command
    requires: [title]
    run:
      - git push --set-upstream origin HEAD
      - gh pr create --title {{title}} --body "Closes #{{issue}}."
done_requires: [commit, title]
needs_human: { call: label_needs_human }
```

`prompts/implement.md`:

```markdown
Implement the GitHub issue the run's work item names, test-first, and commit it. When the run
holds a failure or notes, address them first.
```

`prompts/title.md`:

```markdown
Read the commit on this branch and write a pull request title for it, one line, to
.scratch/pr-title.txt.
```

`hooks/checks.py`:

```python
import subprocess
from pathlib import Path

from kinby.contracts import StepEnding
from kinby.plugins.hooks import HookResult, StepEnd, hook


def _git(folder: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=folder, capture_output=True, text=True)
    return done.stdout.strip()


@hook
def record_commit(end: StepEnd) -> HookResult | None:
    if end.ending is not StepEnding.CLEAN:
        return None
    if _git(end.workspace, "rev-list", "--count", "origin/HEAD..HEAD") in ("", "0"):
        raise RuntimeError("The step made no commit.")
    return HookResult(values={"commit": _git(end.workspace, "rev-parse", "HEAD")})


@hook
def record_title(end: StepEnd) -> HookResult | None:
    title = end.workspace / ".scratch" / "pr-title.txt"
    if not title.is_file():
        raise FileNotFoundError("The step wrote no .scratch/pr-title.txt.")
    return HookResult(values={"title": title.read_text(encoding="utf-8").strip()})
```

`tools/github.py`:

```python
import json
import subprocess

from kinby.plugins.tools import tool


def _gh(*arguments: str) -> str:
    done = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=True)
    return done.stdout


@tool(write=False)
def find_issues(signal: dict) -> list[dict[str, int]]:
    """The open issues labeled ready-for-agent, each a work item."""
    found = json.loads(_gh("issue", "list", "--label", "ready-for-agent", "--json", "number"))
    return [{"issue": issue["number"]} for issue in found]


@tool(write=True)
def label_needs_human(issue: int) -> None:
    """Mark the issue as waiting for the user."""
    _gh("issue", "edit", str(issue), "--add-label", "ready-for-human")
```
