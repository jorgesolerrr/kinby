# Any edit to a routine revokes its grants

Ticket [#161](https://github.com/jorgesolerrr/kinby/issues/161): a routine that clicks in the browser or calls an MCP tool every hour asks every hour under `ask`, so the user's only options are to approve each run or raise the instance's whole mode. The research in [#918](https://github.com/jorgesolerrr/kinby/issues/918) found that OpenClaw lets "Always allow" on a scheduled job's approval mint a grant bound to that job, revoked when the job changes. kinby adds the **routine grant**.

## Decision

- **An approval raised in a routine's turn offers "Always allow in this routine".** Taking it records a routine grant: the gate allows that tool in that routine's turns from then on, under any mode the instance's ceiling allows.
- **A grant is bound to the routine and the tool, not to the arguments.** Search, browser and MCP arguments change on every run.
- **Any edit to the routine revokes all its grants**, whether the user or the agent made it. The agent cannot widen what the user approved by rewriting the routine it was approved for.
- **The web app lists each routine's grants, with a revoke action.** A grant never applies to a turn the user starts.

## Considered options

- **Approve each run, or raise the mode.** Rejected. The first defeats unattended work, and the second opens every tool to every turn.
- **Bind the grant to the exact arguments.** Rejected. It would never match a second run of a browser or search routine.
- **Keep a grant across the agent's edits.** Rejected. A routine's prompt decides what the tool is used for, so a new prompt needs a new allow.
