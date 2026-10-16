---
name: hub-report
description: Answer "how is the hub going?" from the instances' statuses, usage, factory runs and the threads that need the user. Use when the user asks how the hub, an instance or a factory is doing.
---

The user asks so they don't have to open each instance. Lead with what needs them, then what is wrong, then the rest in a line or two. Every read here runs through `hub_read` and asks nothing.

## 1. Gather

1. `instance.list`: each instance's `persona_name`, its `intended_state` and `process`, `setup_pending`, its `notices`, and `needs_human`, the count of its factory runs stopped for the user.
2. For each running instance, `thread.list` with its `instance_id`. A thread whose `status` is `awaiting_approval` waits on the user. One whose status is `failed` closed with an error. The sidebar filter, the default, lists both.
3. `factory.list`, then `factory.run.list` for each factory. Runs with status `needs-human` or `parked` wait. `factory.run.get` shows a run's steps and the summary of its last attempt.
4. `stats.summary` with `since` set to the start of the period the user means, a day unless they say otherwise. It holds each running instance's usage, the API cost, each subscription's runs per plan window and any plan limit still ahead. It names the instances it could not count.
5. `routine.list` for an instance whose work matters to the question. A routine whose `failure_count` is above zero is failing now. Its `last_failure` is the reason of its latest failure, and it stays after the routine recovers, so read it only to explain a failure.

Done when every instance the hub lists is either counted or named as stopped or unreachable.

## 2. Look closer where something is wrong

- An instance that should run and does not: `instance.status`, then `instance.logs` with a `tail`.
- A pending setup: `instance.status` on that instance. Its `setup` holds the logins, the secrets and the missing lists of repository commands. Name what is missing, and say that the user finishes it on the instance's setup card.
- A notice: an instance update in the web app answers it.
- A failed thread: `thread.turn.list` on that instance, for the turns to point at.

## 3. Report

- What needs the user, each with where to act: the thread link `/instances/<instance_id>/threads/<thread_id>` for an approval or a failure, the factory run for a run that needs a human.
- What is broken and the likely cause, in one line each.
- Usage for the period: cost, the runs each subscription made per plan window, and each subscription in `limits`, with the time it resets. A subscription that is not in `limits` has no limit you can see, so leave out how close it is to one.
- Everything else in a sentence, such as "the other four instances ran their routines and need nothing".

Say plainly what you could not read, such as a stopped instance or one that did not answer. Never fill the gap with a guess.
