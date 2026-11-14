---
name: write-factory
description: Write or edit a factory with hub_write's factory_write operation, its steps, checks, tools and prompts, so it passes the factory check, then set it up as a line. Use when the user wants a factory designed, created or changed.
---

A factory is a folder of files: a sequence of steps the hub runs to carry a work item, such as one issue, from start to finish. It runs on a workbench, which holds the image, the secrets, the workspace and its lists of repository commands. Set up on one workbench under an owning instance, it is a line. The factory holds behavior only. Nothing in it names one repository's commands or conventions, and its prompts name no skill a workbench picks. Each workbench keeps those itself.

## Read before you write

Read `factory.list`, then `factory.get` for the factory to change or the one closest to what the user wants. It returns every file of the folder and the folder's `hash`. Read `factory.run.list` to see how a factory's runs went before changing it.

This skill's `references/factory.md` covers every `factory.yaml` field, the step kinds and engines, send-back, retries and needs human, the gate in each step, checks and tools with their imports, the step endings and the run folder, with a worked example that passes the factory check. Read it with the `skill` tool, `file` set to that path. Never read kinby's installed source to learn these.

A factory whose file declares `instances` is in an older form, still run by the instances that picked its roles. Rewriting it in this form takes it from them, so write new factories in this form, and ask the user before you convert an old one.

## The folder

- `factory.yaml` declares the factory. Its `name` is the folder's name.
- `prompts/` holds the agent steps' prompts, such as `prompts/implement.md`.
- `hooks/` holds the checks, `tools/` the tools, and `skills/` skills of the factory's own that its agent steps are offered.

## factory.yaml

- `requires` says what a workbench must provide: the image recipe, `secrets`, named `values`, the names of the lists of repository `commands` the steps run, `logins` and a `browser`.
- `intake` declares what fires the line's intake, a `schedule`, a `delivery` or both, and the tool it calls to find work items. Setting the line up creates the intake routine in the owner.
- `work_item` declares the values each work item carries, by name and type: `str`, `int` or `bool`.
- `steps` is the sequence of steps, run in order, each on the workbench in the run's folder, which kinby cuts. No step names where it runs.
- `done_requires`, `needs_human`, `ended` and `closing` say when a run is done, and what the factory's tools do when it stops for the user or ends.

## Steps

Every step has an `id`. It may declare `requires`, the values it needs from the work item or an earlier step, and `results`, the values it hands on by name and type. A step whose required value no earlier step produces fails the check.

- `agent`: a model doing the work from a `prompt`, run by its `engine`: `claude` or `codex`, a coding client that keeps a session a later step can `resume`, or `kinby`, a kinby turn that goes through the gate. `model` and `effort` are optional, and the line's model fills a model left out.
- `command`: commands in the run's folder, each without a shell. `{{name}}` stands for a run value, and `{ commands: <name> }` runs the workbench's list of repository commands of that name.
- `code`: a tool of the factory's folder the model never sees, named in `call`. A mapping it returns is the step's values.
- `wait`: parks the run until a signal matches one of its `signal` filters, or until `quiet` or `deadline` passes.
- `approve`: parks the run and notifies the user with its `summary`, which can name run values, until they approve it or send it back with notes.

A step's result comes from the world, never from what the agent says it did. An `agent`, `command` or `code` step names a `check` to record it: a hook of the factory's folder that reads the run's folder and returns the step's values and outcome, or raises to fail the step. A step with nothing to record names none.

What happens next: `failed: { retry: <n> }` runs a failed step again with what failed as the value `failure`, and `failed: { back: <step>, max: <n> }` sends the failure to an earlier step instead. `outcomes` maps each outcome a check or code step names to `next`, `done`, `stop` or `{ back: <step>, max: <n> }`. A step the user sends back reads their notes as `notes`, so no step saves them.

## Write it

Call `hub_write` with `factory_write` and, as its arguments, the factory's `name`, the files to write by their path in the folder, each whole, and the `hash` from `factory.get`. Use `hash` null to create a new factory. Files you leave out stay as they are, and no write removes one, so tell the user which file to delete when one should go. Editing the shipped factory writes the hub's own copy of it.

The hub runs the factory check before it keeps anything. A factory that fails it is refused with every problem listed, and the last good version stays. Fix each problem and write again.

Done when `factory_write` returns the factory and `factory.check` on it reports no problems. A changed factory reaches every line at its next step, with nothing to update.

## Set it up as a line

A new factory runs nothing until it is set up as a line: on one workbench, under an owner, with a model and a daily budget. Offer it to the user once the factory is written. Setup is one `line_setup` write, run as the kinby-hub skill's Setting a line up says: `line.setup.describe` lists what the factory requires and the workbench lacks, and one call answers it, creating the workbench when the user has none.
