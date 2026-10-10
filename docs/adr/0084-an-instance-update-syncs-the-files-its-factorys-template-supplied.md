# An instance update syncs the files its factory's template supplied

Supersedes [ADR 0039](0039-package-updates-preserve-instance-configuration.md) for factories. Amends [ADR 0052](0052-an-update-prepares-the-candidate-then-replaces-the-container.md).

Installing a factory copies each **instance template** into its instance once. The hub reads the factory file and its prompts from the factory's folder on every use, so a factory change reached the next step while the instance kept its old tools, hooks, skills and `SYSTEM.md`. After [#596](https://github.com/jorgesolerrr/kinby/pull/596) the software factory's `implement` prompt named `/instance/skills/ponytail/SKILL.md`, and the coder installed before it had no such file. Twice the fix was a hand copy onto the box ([#593](https://github.com/jorgesolerrr/kinby/issues/593), [#619](https://github.com/jorgesolerrr/kinby/issues/619)).

ADR 0039 never had this problem. A package kept its tools and skills in the installed distribution, and an update replaced them. A factory copies everything, so the update has to carry the files.

## Decision

- **The hub records what the template supplied.** At install it stores, for each instance, the hash of every file the template supplied. That record is the instance's baseline. `kinby.toml` is not in it: the install merges the template's manifest with the setup values, and from then on it is the instance's configuration.
- **An instance update syncs the files.** `instance.update` on a factory's instance compares three things for each file: the baseline, the file in the instance, and the template the hub serves now. It works the changes out before it disturbs the container, and a factory that fails the **factory check** fails the update there. It writes them while the container is down, then stores the new baseline. The update's steps list each file written, removed and kept.
  - The template's file changed, and the instance's still matches the baseline: the update writes the template's file.
  - The template dropped the file, and the instance's still matches the baseline: the update removes it.
  - The instance's file no longer matches the baseline: the update keeps it and lists it.
  - The template never supplied the file: the update leaves it alone. Skills and routines the agent wrote stay.
- **A file the instance changed is kept.** The user edits `SYSTEM.md` from the web app (ADR 0064), and the failure policy and the instance tools write routine files. An update must not fail a deploy over an edit like those, or overwrite one.
- **An instance with no baseline takes every template file once.** An instance installed before the hub kept baselines has nothing to compare against. Its first update writes every file the template supplies, except `kinby.toml`, lists them, and stores the baseline. It removes nothing.
- **The update refreshes the stored setup.** The hub kept each instance's declared setup as installed. The update now stores the template's current setup fields and subscription logins, so a secret a new tool needs shows as **pending setup**. Pending setup never blocks a start.
- **A notice says when the factory moved past the instance.** An instance whose baseline differs from the template the hub serves carries an **instance notice**. A file the update kept holds the notice up until the user takes the template's version or the template changes again.

## Considered options

- **Ownership by folder**, as ADR 0039 had it: the factory owns `tools/`, `hooks/` and `skills/`, and the instance owns the rest. Rejected. `SYSTEM.md` was part of both hand copies, and the agent writes its own skills into `skills/`. Removing a skill the template dropped needs a record of what the template supplied anyway.
- **An operation of its own**, per instance or for a whole factory. Rejected. CI already runs `instance.update` on every green push to `main` with a token that reaches nothing else (ADR 0057), so the sync rides on the deploy with no new method, scope or command.
- **Reinstalling the factory.** Rejected. A new instance has a new id, and its memory, threads and volumes belong to the old one.
- **A changed file fails the update, or is overwritten.** Rejected for the reasons above.
- **Refusing a run whose prompts name a file the instance lacks.** Rejected. A prompt is free text, and the factory check cannot tell which instance files it names.

## Consequences

- An **instance update** no longer leaves the whole instance directory alone. It still never touches `kinby.toml`, the secrets, memory, threads, or a file the template did not supply.
- A hub's own copy of a factory syncs the same way, since the update reads whichever folder the hub serves under the factory's name. Editing that copy brings no new revision, so the instances take it on their next update.
- A customized copy of a shipped factory still does not follow the shipped one. That is a separate question.
- The first update of an instance with no baseline can overwrite a hand edit to a template file. On the playground hub those files are hand copies of the template.
- A template that names another image recipe does not move the instance onto it.
