# A coding client keeps its state in the instance directory

Claude Code wrote its sessions under `/root/.claude`, in the container's writable layer. An instance update recreates the container, which deleted every session. The hub still held the session ids, so each later step that resumed one failed with `No conversation found with session ID` until a human sent the run back ([#635](https://github.com/jorgesolerrr/kinby/issues/635)).

## Decision

- **The instance runs Claude with `CLAUDE_CONFIG_DIR` set to `claude` in its state folder.** The instance directory is a bind mount every instance already has, so its sessions survive an update, a restart and a recreate with no change to the instance's storage. The software factory signs Claude in with `CLAUDE_CODE_OAUTH_TOKEN`, so it needs nothing from `/root/.claude`.
- **A step whose Claude session is lost starts a new one.** When a resumed run exits with that error, the instance runs the same prompt once more without `--resume`. The step ends as that run ends, and its summary says the session was lost. No other failure gets a second run.
- **Later steps continue the newest session.** A client step resumes the newest session recorded by the step it names or by any step that resumes that step.

## Considered options

- **A volume for `/root/.claude` that the factory's instance template declares.** Rejected. It needs a change to the instance's storage, and the existing coders would keep losing sessions until they were reinstalled.
- **Sending the run back to the step that owns the session.** Rejected. It repeats that step's work to rebuild context the run can live without.

## Consequences

- A factory that signs Claude in with a login volume at `/root/.claude` no longer reaches it from a client step. No shipped factory does.
- Session files grow in the instance's state folder. Nothing deletes them yet.
- Codex keeps its state in `/root/.codex`, which its login volume already mounts. It has no lost-session fallback.
