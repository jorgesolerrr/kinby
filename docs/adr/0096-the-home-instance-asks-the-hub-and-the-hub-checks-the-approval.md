# The home instance asks the hub, and the hub checks the approval

Amends [ADR 0049](0049-a-cookie-session-for-the-browser-and-a-control-token-per-instance.md) and [ADR 0077](0077-an-instance-reaches-the-hub-only-on-its-own-intake-route.md).

Ticket [#525](https://github.com/jorgesolerrr/kinby/issues/525): the **home instance**, persona "Kinby", should make the hub easy to drive. It creates instances, writes their routines, writes factories and library skills, and reports on all of it. ADR 0049 kept every hub scope out of instances, and ADR 0077 let an instance call the hub on one route for one method. The home instance needs more than that, without the access token ever entering it.

## Decision

- **The registry grants the role.** The instance registry marks exactly one instance as the home instance, never its manifest, so nothing the agent writes can promote an instance. The hub creates one on start whenever none is marked, existing hubs included. It can be stopped and renamed, never removed, and the role never moves.
- **One hub route per instance, scopes from the registry.** ADR 0077's route widens. An instance presents its control token on `/instances/<id>/hub`, and the hub grants the scopes its registry gives that instance: `factory:intake` to a factory's instances, `hub:home` to the home instance. `KINBY_HUB_URL` replaces `KINBY_INTAKE_URL`. The hub records the requesting instance on every lifecycle operation it starts.
- **`hub:home` is an allow-list.** Reads: every `hub:read` method, and the relayed reads of any instance (memory, threads and turns, routines, usage, stats, prompt, manifest, permissions, config history). Writes: create an instance through onboarding; start, stop and recreate one; set an instance's picks and prompt; write, enable, run and delete its routines; start a turn in it; write a library skill, add one from git, move an entry; write a factory's files, which also creates a factory. Never: remove, restore, delete, adopt or update an instance, set any secret, change an instance's permissions or manifest, or act on a factory run.
- **No secret value passes through the model.** When Kinby creates an instance, a secret field names a hub secret or stays empty and becomes **pending setup**. The hub refuses `secrets` and `save_to_hub` from `hub:home`.
- **Every hub write asks, and the hub checks the answer.** The gate asks on a **hub tool** write in every permission mode, full-access included, and no `permissions.toml` rule lowers that. The answer reaches the instance through the hub's relay (`thread.approval.respond`), so the hub sees it. The hub accepts a `hub:home` write only with an approval id it saw the user approve, for those exact arguments, once. A late approval of a dead turn revives nothing.
- **Hub tools exist only on turns the user starts.** Routine and factory-step wakes run without them.
- **The tools.** One read tool, `hub_read(method, params)`, over the allowed reads. One typed tool per write, so each approval shows the operation that will run. No MCP.

## Considered options

- **The home instance holds the access token or a hub scope on `/ws`.** Rejected. ADR 0049's line holds: an agent with bash would hold every hub method.
- **Enforce the ask only in the home instance's gate.** Rejected. The control token sits in the instance's `.env`, so a bash script could call the route and skip the gate, and `factory.edit` would put hub-run hook code within its reach.
- **A second approval surface on the hub.** Rejected. The hub already relays the approval the instance raises. Checking that one gives the same guarantee with one place to answer.
- **The ordinary gate under the thread's mode, as ADR 0028 has for instance tools.** Rejected for hub tools. An instance tool's worst case stays inside the instance's container, and a hub tool's does not.
- **Change other instances only through a turn in them.** Rejected for routines, prompt and picks. Those are setup Kinby is meant to do, and one approval in Kinby's own thread is plainer than a request answered in another instance's thread. A turn in the target stays for work that needs its own judgment or memory.

## Consequences

- The control token now opens more than intake, but only what the registry grants that instance.
- The hub keeps the approvals it relayed until each is used or its turn ends.
- Kinby reads other instances' memory and transcripts. What it reads never enters its own recall.
- kinby ships five library skills the home instance picks by default: `kinby-hub`, `create-instance`, `write-routine` (extended to other instances), `write-factory` and `hub-report`.
