# A shell read runs unasked, and a routine can be granted its code step

Amends [ADR 0014](0014-gate-policy-is-per-tool-rules-under-mode-presets.md), [ADR 0022](0022-routine-code-steps-require-gate-allow.md), [ADR 0112](0112-any-edit-to-a-routine-revokes-its-grants.md) and [ADR 0115](0115-a-routine-parked-on-an-approval-holds-only-its-own-thread.md).

Ticket [#1220](https://github.com/jorgesolerrr/kinby/issues/1220): Finance's routines run with `mode: auto` so they can work overnight. A manual run of `weekly-lesson` parked on `cd /instance/workspace; ls -R . | head -50; ls learning` and waited for Jorge. `auto` allows a write unasked only when the tool declares paths inside the workspace, and `bash` declares none, so every call asks, `ls` included. `code_run` asks for the same reason. A routine's code step is worse off: it needs an allow from the gate, the code-step evaluation ignored routine grants, and the model is never offered the step, so no approval and no grant could ever reach it. Under `auto`, an authored tool's code step failed every turn.

## Decision

- **A shell read is a read under `ask` and `auto`.** It is one or more simple commands joined only by `|`, `;`, `&&`, `||` or newlines. Every program is on a fixed allowlist of programs that only read, and `git` counts only with `status`, `log`, `diff` or `show`. No command passes a flag that makes its program write or run another program. The command holds no expansion, subshell, group, background job or input redirection, and redirects output only to `/dev/null` or onto another output. No argument names a path outside the workspace. The gate allows a shell read with rule `bash.read`.
- **The classifier fails toward asking.** A command it cannot parse or does not recognise is gated as before.
- **Rules still come first.** The bash deny and ask patterns and a `tools.bash` rule decide before `bash.read`. `read-only` still denies bash, and `full-access` is unchanged.
- **The allowlist is kinby's**, like the shipped deny patterns. `permissions.toml` cannot extend it.
- **Other bash and every `code_run` call stay writes.** They ask under `ask` and `auto` unless a routine grant or a `tools.<name>` rule allows them.
- **A code step still requires an allow, and honours its routine's grants.** A granted code step runs in any mode up to the ceiling but `read-only`, as a model's call does. A code step the gate doesn't allow still fails the turn, and the failure tells the user they can grant it from the routine's page.
- **The user can grant a routine its own code step from the routine's page.** `routine.grant.add` records the grant and refuses any tool that is not the routine's code step. The grant is listed and revoked like one made from an approval, and any edit to the routine revokes it.

## Considered options

- **Allow all bash under `auto` unless a deny or ask pattern matches.** Rejected. Bash inherits the process environment with every secret from the instance's `.env`. It can rewrite `permissions.toml`, which holds the mode and the ceiling, and the files holding the read-only marks and the routine grants. It can also `curl` any host. A prompt-injected routine would turn `auto` into `full-access`.
- **A routine grant on `bash`.** Rejected. It allows every command in the routine, which is far broader than reading files.
- **Letting a code step park on an approval.** Rejected. The step runs before the model and has no model call to resume, and a routine that parks overnight is the problem this decision solves.

## Consequences

- `bash.read` joins the gate rules recorded in `tool.gated` events.
- [ADR 0115](0115-a-routine-parked-on-an-approval-holds-only-its-own-thread.md) said the gate never parses commands. It now parses them only to tell a shell read, and anything it can't tell asks.
- A shell read can still read any file in the workspace and print it to the model. The workspace is already the model's to read.
- `git` reads the repository the workspace sits in, which may be the instance's own. `kinby init` keeps `.env` and the state directory out of it.
- A routine grant from the routine's page records no approval, so `RoutineGrant.approval_id` is null for it.
- A granted code step keeps its grant when `code_write` changes the code it runs. Revoking it then is left to a later decision.
