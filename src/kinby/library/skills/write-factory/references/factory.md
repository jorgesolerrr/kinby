# The factory reference

Every `factory.yaml` field, what each step kind does, where a run goes next, and what an instance template may hold. The shipped software factory, from `factory.get`, uses most of it.

## factory.yaml

- `name`: the factory's name, the same as its folder's. Lowercase letters, digits and `-`.
- `instances`: each role, by name, with its declaration. At least one. Its template is `instances/<role>/`.
  - `image`: one of the image recipes kinby ships, such as `coder`. Left out, the role runs kinby's base image.
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
- `failed`: `{ back: <step>, max: <n> }`, where a failed attempt is sent instead of running again.
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

A hook is a function in the template's `hooks/`, decorated with `hook` from `kinby.plugins.hooks`. The hook's name is the function's.

- It takes a `StepEnd`: the `instance`, the `workspace` the step ran in, its run worktree when the step names a `branch`, the step's `ending`, the `work_item` and the `results` the run holds so far.
- It returns a `HookResult` with `values` and, when the step has `outcomes`, an `outcome`. None records nothing.
- Raising fails the step.
- It runs for every ending of the step, a failed one included.
- kinby's default hooks need no file. `record_branch` records `branch`, the workspace's current branch. `find_pull_request` records `pr`, the number of the pull request of the run's `branch`.

## Instance templates

`instances/<role>/` is copied into each instance that picks the role at creation.

- It may hold `SYSTEM.md`, `kinby.toml`, `tools/`, `hooks/`, `routines/` and `skills/`, and other text files.
- It may not hold `.state/`, `workspace/`, `.env`, or anything in `memory/` but `memory/profile.md`.
- `SYSTEM.md` is the default of the instance's behavior prompt.
- `kinby.toml` merges into the starter manifest. It may not set `id`, `persona_name` or `state_dir`.
- The factory check loads every tool and hook file, and creates an instance from the template with its defaults.
- The intake routine hands the factory one work item per call: its turn calls `hand_to_factory`, or its code step calls `hand_over` from `kinby.plugins.intake`. The work item holds exactly the `work_item` values, each of its type. A work item matching an open run returns that run.
- The factory names no repository's commands or conventions, and its prompts name no skill an instance picks. Each instance keeps those itself, in its lists of repository commands and its picks.
- An instance update writes the template's changed files into an instance where the instance hasn't changed them, and removes files the template dropped. Files the instance changed stay.

## Run worktrees

A step with `branch` runs in the run worktree of that branch, cut from it the first time a step needs it. kinby never removes one. The factory's own code removes it, usually in its `ended` call.
