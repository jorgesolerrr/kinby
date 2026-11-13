# The factory reference

Every `factory.yaml` field, what each step kind does, where a run goes next, and what an instance template may hold. The shipped software factory, from `factory.get`, uses most of it.

## factory.yaml

- `name`: the factory's name, the same as its folder's. Lowercase letters, digits and `-`.
- `instances`: each role, by name, with its declaration. At least one. Its template is `instances/<role>/`.
  - `image`: one of the image recipes kinby ships. Left out, the role runs kinby's base image. kinby ships one recipe:
    - `coder`: the base image with `gh`, Claude Code, Codex, Bun, Node and pnpm, for `client` steps.
  - `setup_fields`: what an instance of the role asks for at creation, after kinby's built-in `model` and `api_key`. See Setup fields.
  - `logins`: the sign-ins the role runs at creation. See Logins.
  - `suggested_picks`: skills the role's instances are offered, each a skill kinby ships in its library.
- `intake`: the routine that hands the factory its work items.
  - `instance`: the role it runs in.
  - `routine`: its name. It must be `instances/<instance>/routines/<routine>/ROUTINE.md`.
- `work_item`: the values each work item carries, by name and type: `str`, `int` or `bool`. A name is lowercase letters, digits and `_`.
- `steps`: the line of steps, run in order. At least one. See Steps.
- `done_requires`: values the run must hold once its last step ends. A value missing or false fails that step.
- `needs_human`: a tool the hub calls each time a run stops for the user. See Needs human.
- `ended`: a tool the hub calls once when a run ends done or cancelled. See Ended.
- `closing`: the signals that end a run that needs the user, once its work finished elsewhere. See Closing.

### requires

A factory may declare `requires` in place of `instances` and `intake`. Its steps then run on a workbench, and the file is read in that form:

- `requires`: what a workbench must provide. Each field may be left out.
  - `image`: one of the image recipes kinby ships, as under `instances`.
  - `secrets`: setup fields, each without `kind` or `target`. A secret is text or multiline and has no default.
  - `values`: named values that are not secrets, setup fields without `kind` or `target`.
  - `commands`: the names of the workbench's lists of repository commands the steps run. A `command` step's `{ commands: <name> }` names one of them.
  - `logins`: as under `instances`. See Logins.
  - `browser`: `true` when the steps need a browser.
- Steps take no `in`, and a `command` step no `branch` or `hook`. Only `agent`, `command`, `code`, `wait` and `approve` steps.
- An `agent` step runs a model on the workbench, in the run's folder:
  - `engine`: `claude` or `codex`, a coding client, or `kinby`, a kinby turn. A kinby turn has bash and the file tools, in the run's folder only, and goes through kinby's default gate policy. When the gate asks about a call, the run parks and the user gets a notification that names it. Their answer resumes the turn. The step's `check` runs once the turn ends.
  - `prompt`: a file of the factory's folder, as for an `agent` step in an instance.
  - `model`: the client's model, such as `claude-opus-5-5`. Left out, the line's model when the engine runs its provider's models, `anthropic` for `claude` and `openai` for `codex`, and the client's default otherwise.
    For `kinby`, a `provider:model`, such as `anthropic:claude-opus-5-5`, and left out, the line's model. The workbench must hold that provider's `<PROVIDER>_API_KEY`, and setting up the line refuses one without it. List it under `requires.secrets`.
  - `effort`: as for `client`. The `kinby` engine ignores it, as it does `timeout`.
  - `resume`: an earlier `agent` step on the same coding client whose session this one continues. The `kinby` engine keeps no session.
  - The client is offered the skills of the factory's folder, as `skills/<name>/SKILL.md`, and the workbench's picks. Its transcript is kept on the workbench, one per attempt.
- An `agent`, `command` or `code` step may name a `check`: a hook of the factory's folder, as `hooks/<file>.py`, which runs however the step ended, though not while its kinby turn waits on an ask. See Hooks. kinby's default hooks are no check.
- `needs_human` and `ended` take only `call`.
- Tools live in the factory's folder, as `tools/<file>.py`, and a `code` step, `needs_human` or `ended` calls one of them.

### Setup fields

Each entry of `setup_fields`:

- `name`: a secret's name is an environment variable name. Naming a built-in field, `model` or `api_key`, gives it another default and keeps its kind and type. `behavior_prompt` is refused: it starts from the template's `SYSTEM.md`.
- `label` and `description`: what the user sees. Neither may be empty.
- `kind`: `config` lands in the instance's configuration, `secret` in its secrets.
- `type`: `text`, `multiline`, `boolean`, `integer`, `choice`, `email` or `url`. A secret is `text` or `multiline`.
- `required`: whether creation needs a value.
- `default`: the value when the user sends none, of the field's type. A secret never has one.
- `choices`: the values a `choice` field offers. Only a `choice` field has them.
- `target`: where a `config` field lands, as `file`, always `kinby.toml`, and `key`, a dotted key in it such as `workspace.source`. Every `config` field but a built-in one names a target. A secret names none.

### Logins

Each entry of `logins` is a sign-in run in a setup container at creation:

- `id`: names the login's volume, so letters, digits, `.`, `_` and `-`, starting with a letter or digit.
- `label` and `description`: what the user sees.
- `command`: what the setup container runs, as a list of arguments.
- `volume`: the absolute path the login's volume mounts at, in the instance and in the setup container.
- `prompt_pattern`: a regular expression with the named groups `url` and `code`, which finds the sign-in URL and the one-time code in the command's output.

## Steps

Every step has these fields:

- `id`: unique in the factory. Lowercase letters, digits, `_` and `-`.
- `kind`: `agent`, `client`, `command`, `code`, `wait` or `approve`.
- `requires`: values the step needs, from the work item or an earlier step's `results`. The check refuses a value neither holds. A value false counts as missing.
- `results`: the values the step hands on, by name and type, as in `work_item`.
- `outcomes`: what each outcome a hook or code step names does next. See What happens next.
- `retry`: how many times a failed attempt runs again.
- `failed`: `{ back: <step>, max: <n> }`, where a failed attempt is sent instead of running again, or `{ retry: <n> }`, how many times it runs again. Either way the next attempt reads what failed as `failure`.
- `timeout`: a duration such as `90s`, `60m`, `2h` or `7d`.

Each kind adds its own:

- `agent`: a kinby turn on a new thread in the instance.
  - `in`: the role whose instance runs it.
  - `prompt`: the turn's message, a file in the factory's folder such as `prompts/implement.md`.
  - `hook`: records the step's values.
  - The turn runs in the instance's default permission mode. A write the gate asks about parks the step until the user answers. `timeout` doesn't apply: the instance's turn budgets bound the turn.
- `client`: a coding client run in the instance's container, with no kinby turn around it.
  - `in`, `prompt` and `hook`, as for `agent`.
  - `client`: `claude` or `codex`.
  - `model`: the client's model, such as `claude-opus-5-5`. Left out, the client's default.
  - `effort`: `low`, `medium`, `high`, `xhigh` or `max`. Codex has no `max`. Left out, the client's default.
  - `resume`: an earlier `client` step of the same role and client whose session this one continues.
  - `branch`: `"{{name}}"`, the run value naming the branch whose run worktree the client runs in. Left out, it runs in the workspace.
  - kinby's gate is not involved: Claude Code edits and runs commands unasked, Codex runs without approvals or sandbox. Without `timeout` an attempt stops after 60 minutes.
- `command`: commands in the instance's workspace, each without a shell.
  - `in`: the role.
  - `run`: the commands, in order. A `{{name}}` in a command stands for that run value, as one argument. An entry `{ commands: <list> }` runs the instance's own list of repository commands its `commands` names, as written. Exit code zero passes. The first that fails fails the step, with the command and the last 2,000 characters of its output as the attempt's summary.
  - `hook`: optional. Without one the step records no values.
  - `branch`: as for `client`.
  - No gate. Without `timeout` the commands run as long as they take.
- `code`: a tool of the template the model never sees.
  - `in`: the role.
  - `call`: the tool's name. Its parameters named like a run value receive it, and `run_id` the run's id. A mapping it returns is the step's values, and a `HookResult` also names an outcome. Raising fails the step.
  - No gate: the tool runs even when it writes.
- `wait`: parks the run, holding no instance.
  - `signal`: filters, at least one. A signal matches a filter when it carries every field of the filter with the same value and type. A field reads `routine`, the routine the signal came in on, `headers.<Name>` or `body.<key>.<key>`. A value `"{{name}}"` stands for that run value. A match moves on and records nothing.
  - `quiet`: a duration. With no signal for that long, the wait moves on by itself and records `quiet` as true.
  - `deadline`: a duration. With no signal for that long, the wait fails.
- `approve`: parks the run until the user approves it or sends it back.
  - `summary`: what the user is asked to approve.
  - Approving moves on and records nothing. The user can send the run back to any earlier step.

## What happens next

- An attempt that ends clean and names no outcome goes to the next step. After the last step, the run is done once `done_requires` holds.
- `outcomes` maps a named outcome to `next`, `done`, `stop` or `{ back: <step>, max: <n> }`.
  - `next`: the next step. On the last step it is `done`.
  - `done`: the run is done once `done_requires` holds.
  - `stop`: the run ends cancelled, without `done_requires`.
  - An outcome the step doesn't map fails the attempt.

### Send-back

`{ back: <step>, max: <n> }`, in `outcomes` or `failed`, sends the work to an earlier step:

- `back`: the step, which must come earlier. `max`: how many times this edge may send work back, at least 1. One more makes the run need the user.
- The run drops every value the target step and the steps after it recorded, except the sending step's own, which travel back with the work.
- `failed` also records what failed, the attempt's summary, as the value `failure`.
- A step declares `retry` or `failed`, never both.

### Retries

- Without `retry`, an `agent` or `client` step runs once more after a failure, and other kinds never do. `retry: 0` never runs again.
- `failed: { retry: <n> }` runs the step again as `retry: <n>` does, and records what failed, the attempt's summary, as `failure`, so a redraft reads why its draft failed.
- The count is per step over the whole run, until the user retries or sends the run back.
- A timed-out attempt never runs again and is never sent back.
- A coding client that hits its subscription limit waits for the reset and runs again without using a retry.

### Needs human

A run needs the user when a failure has no retry left, an attempt times out, a send-back reaches its `max`, a step's instance or `{ commands: <list> }` is missing, or the factory or the step no longer exists. A value a later step needs and no step recorded fails the step that should have recorded it.

The hub then notifies the user, who can retry the step, send the run back to an earlier step, or cancel it. Handing the same work item in again also retries it.

If `needs_human` is set, the hub calls it: `in`, the role whose instance holds the tool, and `call`, the tool. Its parameters named like a run value receive it, `run_id` the run's id, `step` the step the run stopped at, and `summary` that attempt's summary. A failed call is tried again until the run moves on.

### Ended

If `ended` is set, the hub calls it once a run ends done or cancelled, by any path. `in` and `call` as for `needs_human`. Its parameters receive the run values and `run_id`, never `step` or `summary`. A failed call is tried again.

### Closing

`closing` ends a run that needs the user:

- `done`: filters. A signal matching one ends the run done, without `done_requires`.
- `cancelled`: filters. A signal matching one ends the run cancelled.

A filter reads as a `wait`'s does. The signal can come in on any of the factory's instances.

## Hooks

A hook is a function in the template's `hooks/`, or a check in the factory's folder's `hooks/`, decorated with `hook`. The hook's name is the function's. `hook`, `StepEnd` and `HookResult` come from `kinby.plugins.hooks`:

```python
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def record_brief(end: StepEnd) -> HookResult | None:
    if not (end.workspace / "brief.md").is_file():
        raise FileNotFoundError("The step wrote no brief.md.")
    return HookResult(values={"briefed": True})
```

- It takes a `StepEnd`, with these fields:
  - `instance`: the instance the step ran in. None for a check, on a workbench.
  - `workspace`: the `Path` the step ran in: the run's folder on a workbench, its run worktree when the step names a `branch`, else the instance's workspace.
  - `ending`: how the step ended, a `StepEnding` from `kinby.contracts`. See Step endings.
  - `work_item`: the work item's values, by name.
  - `results`: every value the run's earlier steps recorded, by name.
- It returns a `HookResult`, or None to record nothing. Its fields, each with a default:
  - `values`: the values the step records, by name, as its `results` declare them. Each is a `str`, `int` or `bool`.
  - `outcome`: the outcome it names, one of the step's `outcomes`. None takes the next step.
  - `summary`: only a `code` step's tool sets it. A hook's step keeps the summary it ended with.
- Raising fails a step that ended `clean`, and keeps any other ending.
- It runs for every ending of the step but `asked`, a failed one included. It may be `async def`. A plain `def` runs in a thread.
- kinby's default hooks need no file. `record_branch` records `branch`, the workspace's current branch. `find_pull_request` records `pr`, the number of the pull request of the run's `branch`.

### Step endings

`StepEnding` is a string enum. A hook compares `end.ending` with its members or their values:

- `clean`: the step did its work. The outcome says what the run does next.
- `failed`: the step failed. The run tries it again under its `retry` or `failed`.
- `timed-out`: the step ran past its `timeout`. The run never tries it again.
- `interrupted`: the hub stopped while the attempt ran. Only old runs hold it, and it counts as failed.
- `needs-human`: the step cannot run until the user supplies what it lacks. The run stops for them.
- `limited`: the subscription's limit stopped the step's coding client. The run waits for the reset and tries again without using a retry.
- `asked`: the gate asked about a call of the step's kinby turn. The run parks until the user answers, and the answer resumes the turn without using a retry.

## Tools

A tool is a function in the template's `tools/` or in a routine's `run.py`, decorated with `tool` from `kinby.plugins.tools`. Its name is the function's, its description the docstring, and its parameters, with their type hints, are its arguments. It may be `async def`.

```python
from kinby.plugins.tools import ToolContext, tool


@tool(write=True)
async def hand_issue(issue: int, context: ToolContext) -> str:
    """Hand the factory the issue to brief."""
```

The decorator's arguments, all by keyword:

- `write`: required. True for a tool that changes anything, which the instance's permission mode governs.
- `paths`: the names of the parameters that hold a path. kinby resolves each against the workspace before the call, and the `auto` mode lets a write with declared paths inside the workspace run unasked.
- `hub`, `notify` and `view`: these mark kinby's own hub, notify and view tools. A template's tool leaves them out.

A parameter typed `ToolContext` receives the call's context, never shown to the model: `instance`, `workspace`, and `routine`, the routine whose turn or code step runs the tool.

## Intake

The intake routine hands the factory one work item per call. The work item holds exactly the `work_item` values, each of its type. A work item matching an open run returns that run.

- A routine's turn calls the tool `hand_to_factory` with its `work_item`, such as `{"issue": 42}`.
- A routine's code step calls `hand_over` from `kinby.plugins.intake`:

  ```python
  async def hand_over(routine: str, work_item: Mapping[str, str | int | bool]) -> FactoryRun
  ```

  `routine` is the intake routine's name, `context.routine` in its tool. `work_item` maps each value's name to its value. The `FactoryRun` it returns carries the run's `run_id`, `factory` and `status`.

## Instance templates

`instances/<role>/` is copied into each instance that picks the role at creation.

- It may hold `SYSTEM.md`, `kinby.toml`, `tools/`, `hooks/`, `routines/` and `skills/`, and other text files.
- It may not hold `.state/`, `workspace/`, `.env`, or anything in `memory/` but `memory/profile.md`.
- `SYSTEM.md` is the default of the instance's behavior prompt.
- `kinby.toml` merges into the starter manifest, table by table. See Template settings.
- The factory check loads every tool and hook file, and creates an instance from the template with its defaults.
- The factory names no repository's commands or conventions, and its prompts name no skill an instance picks. Each instance keeps those itself, in its lists of repository commands and its picks.
- An instance update writes the template's changed files into an instance where the instance hasn't changed them, and removes files the template dropped. Files the instance changed stay.

### Template settings

A template's `kinby.toml` may set these tables and keys. It may not set `id`, `persona_name` or `state_dir`.

- `models`: `main`, `recap` and `embed`, each as `provider:model`.
- `workspace`: `path`; `source`, the git URL the instance clones on its first start; `snapshots`; `conventions`, with `enabled`, `instructions` and `skills`; and `commands`, the instance's lists of repository commands by name.
- `memory`: `recap`, `every-turn` or `off`.
- `feedback`: `ask`, `every-turn` or `off`. A factory's turns have nobody to answer, so set it `off`.
- `compaction`: `automatic`, and `trigger`, a context size in tokens.
- `tools`: `defaults`, false to switch off every default tool; `off`, default tools to switch off by name; `search`, `brave`, `tavily`, `exa` or `searxng`; and `bash_timeout_seconds`.
- `browser`: true gives the instance the browser tools.
- `routines`: `timezone`, an IANA time zone.
- `serve`: `listen`, as `host:port`.
- `budgets`: a turn's `steps`, `tokens` and `seconds`, and the instance's `usd_per_day`.
- `prices`: by model, `input`, `output`, `cache_read`, `cache_write` and `window`.

## Run worktrees

A step with `branch` runs in the run worktree of that branch, cut from it the first time a step needs it. kinby never removes one. The factory's own code removes it, usually in its `ended` call.
