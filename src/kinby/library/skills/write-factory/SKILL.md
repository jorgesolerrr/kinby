---
name: write-factory
description: Write or edit a factory with factory_write, its steps, hooks, instance templates and prompts, so it passes the factory check. Use when the user wants a factory designed, created or changed.
---

A factory is a folder of files the hub runs as a line of steps. Its instances are ordinary instances that picked one of its roles. It holds behavior only. Nothing in it names one repository's commands or conventions, and its prompts name no skill an instance picks. Each instance keeps those itself.

## Read before you write

Read `factory.list`, then `factory.get` for the factory to change or the one closest to what the user wants. It returns every file of the folder and the folder's `hash`. The shipped software factory is a worked example of every step kind. Read `factory.run.list` to see how a factory's runs went before changing it.

## The folder

- `factory.yaml` declares the factory. Its `name` is the folder's name.
- `instances/<role>/` is the instance template of one role: `SYSTEM.md`, `kinby.toml`, and the `tools/`, `hooks/`, `routines/` and `skills/` the role's instances start with. The hub copies it into each instance that picks the role.
- Prompts for agent and client steps, at any path in the folder, such as `prompts/implement.md`.

## factory.yaml

- `instances` declares each role. `image` names one of the image recipes kinby ships, such as `coder`, and leaving it out builds kinby's base image. `setup_fields` and `logins` are what the role asks for at creation. `suggested_picks` must be skills kinby ships in its library.
- `intake` names the instance and the routine that hands the factory its work items. That routine lives in the role's template and calls `hand_to_factory` with one work item.
- `work_item` declares the values each work item carries, by name and type: `str`, `int` or `bool`.
- `steps` is the line of steps, run in order.
- `done_requires` lists the values a run must hold once its last step ends.
- `needs_human` names a tool, `in` an instance, that the hub calls when a run stops for the user.
- `ended` names a tool the same way, that the hub calls once when a run ends done or cancelled.

## Steps

Every step has an `id`. It may declare `requires`, the values it needs from the work item or an earlier step, and `results`, the values it hands on by name and type. A step whose required value no earlier step produces fails the check.

- `agent`: a kinby turn on a new thread `in` an instance, with a `prompt` file and a `hook`.
- `client`: a coding client run (`claude` or `codex`) in the instance's container, with a `prompt`, a `hook`, and optionally `model`, `effort`, `resume` to continue an earlier client step's session, and `branch: "{{name}}"` to run it in the run worktree of the branch that run value names.
- `command`: commands in the instance's workspace, each without a shell. `{{name}}` stands for a run value, and an entry `{ commands: <list> }` runs the instance's own list of repository commands. Exit code zero passes. `branch: "{{name}}"` runs them in that branch's run worktree.
- `code`: a tool of the template the model never sees, named in `call`. A mapping it returns is the step's values.
- `wait`: parks the run until a signal matches one of its `signal` filters, or until `quiet` or `deadline` passes. A filter reads `routine`, `headers.<Name>` or `body.<key>`.
- `approve`: parks the run until the user approves it or sends it back, showing its `summary`.

What happens next: `retry` sets how often a failed attempt runs again. `failed: { back: <step>, max: <n> }` sends a failure back to an earlier step instead, with what failed as the value `failure`. `outcomes` maps each outcome a hook or code step names to `next`, `done`, `stop` or `{ back: <step>, max: <n> }`. `timeout` is a duration like `60m`. A timed-out attempt never runs again.

## Hooks

A step's result comes from the world, never from what the agent says it did. A hook records it. It is a function in the template's `hooks/`, decorated with `hook` from `kinby.plugins.hooks`. It takes a `StepEnd` (the instance, the `workspace` the step ran in, how the step ended, the work item, the run's values so far) and returns a `HookResult` with `values` and, when the step has outcomes, an `outcome`, or None to record nothing. Raise to fail the step. kinby's default hooks `record_branch` and `find_pull_request` are there without a file.

## Write it

Call `factory_write` with the factory's `name`, the files to write by their path in the folder, each whole, and the `hash` from `factory.get`. Use `hash` null to create a new factory. Files you leave out stay as they are, and no write removes one, so tell the user which file to delete when one should go. Editing the shipped factory writes the hub's own copy of it.

The hub runs the factory check before it keeps anything. A factory that fails it is refused with every problem listed, and the last good version stays. Fix each problem and write again.

Done when `factory_write` returns the factory and `factory.check` on it reports no problems. Then tell the user that an instance takes a new role only at creation, and that existing instances of a changed role get the new template files through an instance update in the web app.
