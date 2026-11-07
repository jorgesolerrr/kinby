# The home instance reads the hub through the CLI and writes through one tool

Amends [ADR 0096](0096-the-home-instance-asks-the-hub-and-the-hub-checks-the-approval.md) and [ADR 0119](0119-a-shell-read-runs-unasked-and-a-routine-can-be-granted-its-code-step.md).

Grilling [#1218](https://github.com/jorgesolerrr/kinby/issues/1218), spec [#1234](https://github.com/jorgesolerrr/kinby/issues/1234). The 18 hub tools were about half of the home instance's tool definitions, sent on every model call. Hub read results were the largest share of what home sent again on later calls. A tool returns its whole result to the model, so home paid for every character before it could pick the part it needed. One `stats.get` of 2.7 MB ended two turns.

## Decision

- **Reads go through bash.** The home instance reads the hub with `kinby hub read <method>`, plus `kinby hub methods`, `kinby hub help` and `kinby hub wait`. The CLI takes the hub route, the scopes and the control token from the instance's environment. It works in any turn, routines and factory steps included. `hub_read` goes away.
- **The gate counts these commands as shell reads.** That holds only for a method in the contract's list of hub reads and relayed reads, the list the hub enforces. Their `name=value` arguments aren't paths.
- **Writes go through one tool.** `hub_write(operation, arguments)` replaces the 17 typed write tools and keeps their names as operations. It validates the arguments before the gate asks. Every hub write still asks in every permission mode, still exists only on turns the user starts, and the hub still runs it only for an approval of that exact operation and those exact arguments.

## Considered options

- **The CLI next to `hub_read`.** Rejected. The model would keep choosing the tool and paying for whole results.
- **Hub writes through the CLI too.** Rejected. Approving a bash command can't give the hub an approval of one operation's exact arguments, so ADR 0096's guarantee would fall.
- **Keep the typed write tools with one-line descriptions.** Rejected. ADR 0096 wanted one tool per write so each approval shows its operation. The approval for `hub_write` shows the operation in its arguments, and no gate rule can lower a hub write, so separate tools buy nothing.

## Consequences

- Routines can read the hub. They always could through bash and the control token in the environment, and now the way is plain.
- How to call each read and write lives in `kinby hub help` and the home skills, not in tool descriptions.
