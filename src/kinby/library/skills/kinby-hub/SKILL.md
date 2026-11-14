---
name: kinby-hub
description: The parts of a kinby hub, what each is for, and how to read them with kinby hub read and change them with hub_write. Read it before you read or change the hub, another instance, the library, a factory or a line.
---

You are the home instance. The hub created you and lets you, and no other instance, reach it on your hub route. You read the hub and every instance it manages with `kinby hub read` in bash, and you ask the hub for changes the user approves with `hub_write`.

## The parts

- **Hub.** The service that runs the user's instances, each in its own container. It keeps the instance registry, the library, the factories and the hub secrets.
- **Instance.** One agent with its own prompt, memory, threads, routines and skills. A hub instance ID, a UUID, names it in every call. The registry marks one instance as home: you.
- **Lifecycle operation.** One tracked change to an instance: create, start, stop, recreate. The write returns its `operation_id`. `kinby hub wait <operation_id>` follows it until it ends, and its steps and their detail say what failed.
- **Pending setup.** What an instance still lacks after creation, such as a secret nobody set or a login nobody finished. It never blocks a start. The user finishes it on the instance's setup card in the web app.
- **Hub secret.** A named value the hub keeps for several instances. You see names only, through `hub.secret.list`. A secret's value never passes through you.
- **Library.** The skills and factories an instance can pick, and the MCP servers the user or you add. kinby ships some, the user writes or adds others from git. A pick copies a skill or an MCP server's definition into one instance. Nothing in the library reaches an instance until it is picked.
- **Factory.** A line of steps the hub runs to carry a work item, such as one issue, from start to finish across its instances. Each of its instances picked one of its factory roles at creation, such as `coder`, and keeps that role for life.
- **Workbench.** The place a factory's steps run: an image, secrets, named values, a workspace with its lists of repository commands, logins and a browser. It is no instance: it has no persona, threads or memory.
- **Line.** A factory set up on one workbench under an owning instance, you unless the user names another. The owner hands the line its work items and remembers what its runs learn.
- **Factory run.** One work item's trip through a factory. A run that ran out of retries, or lacks what only the user can give, is needs human and waits for the user.
- **Routine.** A prompt an instance runs on a schedule, on a signal, or by hand. It lives as `routines/<name>/ROUTINE.md` in that instance.
- **Thread.** One conversation in one instance. Its status is running, awaiting approval, failed or idle. A thread awaiting approval carries the `approval` it waits on: the tool's `name`, its `arguments` and the gate `rule` that asked. Read it to tell the user what they are approving, or why the gate stopped a run.
- **Channel.** Where the hub sends the user notifications, such as a Telegram chat or an email inbox. Each one keeps a list of the instances it mutes and sends nothing from them.

## Reading

Read the hub from bash with `kinby hub read <method> name=value …`. It runs without asking and prints the answer as compact JSON, so pipe it through `jq` and keep only what you need:

```bash
kinby hub read instance.list | jq '.instances[] | {instance_id, persona_name, process}'
```

A value that parses as JSON is taken as JSON, and anything else as a string. `--params` takes all the params as one JSON object, for a nested value. A refused read exits nonzero and prints the code, the message and each field the hub named.

Your first hub command is `kinby hub help`, which lists every read and write. `kinby hub help <name> <name> …` then prints each read's params and result, and each write's arguments, with their types, defaults and descriptions. Run it once for the reads and writes your task needs, before you make one, rather than guess or read kinby's source.

The hub's own reads:

- Instances: `instance.list`, `instance.status`, `instance.logs`, `instance.picks.get`.
- Toolset: `instance.toolset.get`, what an instance can do at a glance: its default tools on or off, its search backend and whether it holds the key, its MCP servers and whether their read-only hints are trusted, its instance code's packages and authored tools with their read-only marks, and whether its browser is on.
- Browser: `browser.profile.list` and `browser.vault.list`, an instance's browser profiles and vault entries by name and site. You never see a vault entry's values, and only the user adds, changes or removes a profile or a vault entry.
- Setup: `image.describe`, `factory.describe`, `hub.secret.list`.
- Library: `library.list`, `library.skill.read`, `library.skill.file.read`, `library.skill.check`, `library.git.preview`, `library.mcp_server.read`. A library skill's `SKILL.md` comes from `library.skill.read` and each of its other files from `library.skill.file.read`, by the path `library.skill.read` lists it under. Never fetch library content from the network, or from the hub's files with bash.
- Factories: `factory.list`, `factory.get`, `factory.check`, `factory.run.list`, `factory.run.get`.
- Lines and workbenches: `line.list`, `line.get`, `line.setup.describe`, `workbench.list`, `workbench.get`.
- Usage: `stats.summary`.
- Channels: `channel.list`, each channel with the instances it mutes.
- Operations: `operation.get`. To follow one until it ends, run `kinby hub wait <operation_id>` instead. It prints the operation once it succeeded or failed, and exits nonzero unless it succeeded. When it times out, the operation is still going: run it again. Never poll with `sleep`.

An instance's own reads take its `instance_id` beside their own params, and the hub relays them: `thread.list`, `thread.turn.list`, `routine.list`, `routine.read`, `prompt.get`, `manifest.get`, `permissions.get`, `config.history`, `memory.list`, `memory.open`, `usage.get` and `stats.get`. They reach the instance only while it runs.

A read that grows, such as a list or a log, answers with a bounded part unless asked for more, and `kinby hub help` shows each bound as a default. A list read says that more entries match with a `cursor`, which you pass back for the next page.

Keep what you read in another instance's memory out of your own. It is that instance's knowledge, so answer from it and move on.

## Writing

Every write is one `hub_write` call: `operation` is one of the names below, and `arguments` holds that operation's values, as `kinby hub help <operation>` prints them. A call whose arguments are wrong comes back naming each wrong field and asks nothing. Each call asks the user every time, in every permission mode. The user answers on the approval the call raises. A "yes" typed in chat approves nothing, so make the call once the plan is clear and let the approval carry it. The hub runs the call only with the arguments the user approved.

- Instances: `instance_create`, `instance_lifecycle` (start, stop, recreate).
- Image: `image_prepare` prepares the image a vanilla instance runs on, when `image.describe` says it is not prepared. It returns a lifecycle operation to follow with `kinby hub wait`.
- Another instance's setup: `instance_picks_set` (its skills and MCP servers, each server with `trust_hints`, which the user decides), `instance_prompt_set`, `instance_routine_write`, `instance_routine_set_enabled`, `instance_routine_run`, `instance_routine_delete`.
- Work in another instance: `instance_ask` starts a turn there on a new thread. Use it for work that needs that instance's own judgment or memory, and give the user the link it returns as a Markdown link, `[a short label](<link>)`, never as code, so the web app opens the thread on a click.
- Library: `library_skill_write` writes a skill's whole `SKILL.md`, whose frontmatter `name` is the skill's name. `library_git_add` takes the address and commit `library.git.preview` returned. `library_move` takes the hash, commit and diff `library.skill.check` returned, so the approval shows the user the diff. `library_mcp_server_write` writes an MCP server's definition, whose `include` names the tools of the server a turn is offered.
- Factories: `factory_write`.
- Lines: `line_setup`, as Setting a line up below runs it.
- Channels: `channel_mute` and `channel_unmute` take a channel's `channel_id` from `channel.list` and an `instance_id`.

A write that takes a `hash` takes the one its read returned: `instance.picks.get` for picks, `prompt.get` for a prompt, `routine.read` for a routine, `library.skill.read` for a skill, `library.mcp_server.read` for an MCP server, `factory.get` for a factory. A stale hash is refused. Read again, redo the change on what you read, and write again.

## Setting a line up

One `line_setup` call sets a factory up on a workbench the user chose, or creates the workbench and sets the factory up on it, so a first line takes one pass.

1. Read `factory.list` and `workbench.list`. Settle with the user the factory, and the workbench or a new one. A factory that declares instances runs in them and takes no line.
2. Read `line.setup.describe` with `factory`, the chosen `workbench_id` or none, and the line's `model` when the user wants one. It lists only what is left to answer: `workbench`, a new one's `name` and `workspace_source`, then `secrets`, `values` and `commands`, the lists of repository commands by name. Done when each required field it lists has an answer, and the user settled the model, the daily budget in `usd_per_day` and the owner in `owner_id`, left out for you.
3. Put a hub secret's name in `hub_secrets` for each secret, by the variable it lists, as `{"GH_TOKEN": "github"}`. When no hub secret fits, the user adds one first. Never put a secret's value in the call.
4. A login it lists, or `browser` true, is something no answer gives: the login is signed in on the workbench, and a browser comes with a workbench created with one. Name it, point the user to the web app, and set nothing up.
5. Call `hub_write` with `line_setup` and these values. A refusal names each wrong field and creates nothing. When the result carries an `operation_id`, run `kinby hub wait` with it: it creates the new workbench, or stores the secrets on the chosen one. A new workbench is stopped until the user starts it.

Some changes stay the user's, and no tool makes them:

- Removing, restoring, deleting, adopting or updating an instance.
- Setting a secret.
- Creating, changing, testing or deleting a channel.
- Changing an instance's permissions or manifest.
- Retrying, sending back, approving or cancelling a factory run.

Name the change and point the user to the web app.
