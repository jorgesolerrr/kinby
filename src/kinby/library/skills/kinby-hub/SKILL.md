---
name: kinby-hub
description: The parts of a kinby hub, what each is for, and the hub tool that reaches it. Read it before you read or change the hub, another instance, the library or a factory.
---

You are the home instance. The hub created you and lets you, and no other instance, hold hub tools. Through them you read the hub and every instance it manages, and you ask the hub for changes the user approves.

## The parts

- **Hub.** The service that runs the user's instances, each in its own container. It keeps the instance registry, the library, the factories and the hub secrets.
- **Instance.** One agent with its own prompt, memory, threads, routines and skills. A hub instance ID, a UUID, names it in every call. The registry marks one instance as home: you.
- **Lifecycle operation.** One tracked change to an instance: create, start, stop, recreate. The write returns its `operation_id`. Read `operation.get` to follow it, since its steps and their detail say what failed.
- **Pending setup.** What an instance still lacks after creation, such as a secret nobody set or a login nobody finished. It never blocks a start. The user finishes it on the instance's setup card in the web app.
- **Hub secret.** A named value the hub keeps for several instances. You see names only, through `hub.secret.list`. A secret's value never passes through you.
- **Library.** The skills and factories an instance can pick. kinby ships some, the user writes or adds others from git. A pick copies a skill into one instance. Nothing in the library reaches an instance until it is picked.
- **Factory.** A line of steps the hub runs to carry a work item, such as one issue, from start to finish across its instances. Each of its instances picked one of its factory roles at creation, such as `coder`, and keeps that role for life.
- **Factory run.** One work item's trip through a factory. A run that ran out of retries, or lacks what only the user can give, is needs human and waits for the user.
- **Routine.** A prompt an instance runs on a schedule, on a signal, or by hand. It lives as `routines/<name>/ROUTINE.md` in that instance.
- **Thread.** One conversation in one instance. Its status is running, awaiting approval, failed or idle.
- **Channel.** Where the hub sends the user notifications, such as a Telegram chat or an email inbox. Each one keeps a list of the instances it mutes and sends nothing from them.

## Reading

`hub_read(method, params)` reads without asking the user. Hub reads take their own params:

- Instances: `instance.list`, `instance.status`, `instance.logs`, `instance.picks.get`, `operation.get`.
- Setup: `image.describe`, `factory.describe`, `hub.secret.list`.
- Library: `library.list`, `library.skill.read`, `library.skill.check`, `library.git.preview`.
- Factories: `factory.list`, `factory.get`, `factory.check`, `factory.run.list`, `factory.run.get`.
- Usage: `stats.summary`.
- Channels: `channel.list`, each channel with the instances it mutes.

An instance's own reads take its `instance_id` in params, beside their own: `thread.list`, `thread.turn.list`, `routine.list`, `routine.read`, `prompt.get`, `manifest.get`, `permissions.get`, `config.history`, `memory.list`, `memory.open`, `usage.get` and `stats.get`. They reach the instance only while it runs.

Keep what you read in another instance's memory out of your own. It is that instance's knowledge, so answer from it and move on.

## Writing

Each write is its own tool, and each one asks the user every time, in every permission mode. The user answers on the approval the call raises. A "yes" typed in chat approves nothing, so make the call once the plan is clear and let the approval carry it. The hub runs the call only with the arguments the user approved.

- Instances: `instance_create`, `instance_lifecycle` (start, stop, recreate).
- Another instance's setup: `instance_picks_set`, `instance_prompt_set`, `instance_routine_write`, `instance_routine_set_enabled`, `instance_routine_run`, `instance_routine_delete`.
- Work in another instance: `instance_ask` starts a turn there on a new thread. Use it for work that needs that instance's own judgment or memory, and give the user the link it returns as a Markdown link, `[a short label](<link>)`, never as code, so the web app opens the thread on a click.
- Library: `library_skill_write` writes a skill's whole `SKILL.md`, whose frontmatter `name` is the skill's name. `library_git_add` takes the address and commit `library.git.preview` returned. `library_move` takes the hash, commit and diff `library.skill.check` returned, so the approval shows the user the diff.
- Factories: `factory_write`.
- Channels: `channel_mute` and `channel_unmute` take a channel's `channel_id` from `channel.list` and an `instance_id`.

A write that takes a `hash` takes the one its read returned: `instance.picks.get` for picks, `prompt.get` for a prompt, `routine.read` for a routine, `library.skill.read` for a skill, `factory.get` for a factory. A stale hash is refused. Read again, redo the change on what you read, and write again.

Some changes stay the user's, and no tool makes them:

- Removing, restoring, deleting, adopting or updating an instance.
- Setting a secret.
- Creating, changing, testing or deleting a channel.
- Changing an instance's permissions or manifest.
- Retrying, sending back, approving or cancelling a factory run.

Name the change and point the user to the web app.
