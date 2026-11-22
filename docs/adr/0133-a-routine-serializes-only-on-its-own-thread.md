# A routine serializes only on its own thread

Amends [ADR 0019](0019-routines-and-signals-start-turns-through-one-wake.md) and [ADR 0115](0115-a-routine-parked-on-an-approval-holds-only-its-own-thread.md).

ADR 0019 made a routine turn run "exclusively against all other turns" and gave no reason. User turns on different threads already ran side by side. On 2026-11-21 the user wrote to the home instance and was refused with `Routine "software_software-coder" is running. Waiting for it to finish.` That routine is the software line's intake ([ADR 0125](0125-a-factory-runs-on-a-workbench-and-an-instance-owns-its-lines.md)). Its model never runs. It only waits on the hub while the workbench scans GitHub, and for that whole wait the user could not talk to their own instance. Factory steps already run on the workbench, out of the instance's way. The intake routine was the last piece of factory work that could still block a chat.

## Decision

- **A turn serializes only on its own thread, whoever started it.** A routine run, a user turn, a home instance's ask and the answer to a parked routine's approval all start while other threads are running. Two turns on one thread still go one at a time.
- **The scheduler fires on time.** It does not wait for the instance to go idle, and it does not skip a due routine because other turns are running. It skips a routine only while that routine's own previous run is live. A delivery for that routine waits as a queued signal, as it does today.
- **A workspace revert still needs the instance idle**, because every thread shares the workspace. When only routine runs are in its way, the revert can interrupt them on the user's say-so. It never interrupts a user's turn. It refuses and names that turn instead.
- **Consolidation skips a node that changed after its stage turn read it.** The turn's other facts are still written. A chat can now write a node while consolidation is working, and without this check consolidation's last write would erase the chat's.

## Considered options

- **Routines stay exclusive, and a routine can declare itself concurrent.** Rejected. Every routine would get a flag nobody can choose well. User turns already share the workspace with nothing isolating them, so a routine brings no new kind of risk.
- **Exempt only the intake routine.** Shipped as the narrow fix ahead of this decision, and removed by it. A dreaming stage or a news digest blocks a chat just the same.
- **Run the intake on the hub, with no routine.** Rejected for the reason ADR 0125 gave: the hub keeps no scheduler or receiver of its own.
- **A revert that waits for the instance to go idle.** Rejected. With routines firing freely the instance may rarely be idle, and the user would be left waiting on a request with no end.

## Consequences

- What runs on one instance at once is bounded only by its threads. The container's memory and CPU, and the model provider's rate limits, are shared by all of them.
- The daily cost budget is checked against closed turns before a turn starts ([ADR 0020](0020-budgets-fail-the-turn-and-refuse-unpriced-cost.md)), so parallel turns can together overshoot it. The overshoot is bounded by what the live turns spend. Each turn's own budget still caps it.
- A turn's workspace snapshot diff includes whatever parallel turns changed in between, and reverting a turn undoes them too. ADR 0115 already accepted this for parked routines.
- Two turns writing the same workspace file or memory node race, and the last writer wins. Consolidation's check covers the one case where an unwatched turn silently erases the user's work.
- Running several factory steps at once on one workbench is a separate question (#657).
