# A call the instance never took is no attempt

A step's call is undelivered when the instance provably did not take it. Either the hub never sent it, because the instance is not active, has no address, or the control socket did not open, or the instance refused it with `INSTANCE_DRAINING`. An undelivered step records no attempt and spends none of its `retry`. The run stays queued at the step, and the hub offers the call again with backoff, from 5 seconds doubling up to 5 minutes, for as long as the instance is installed. The wait holds the instance's queue, so its steps still run in the order they were queued. A hub restart during the wait finds the run queued with no attempt to interrupt.

A socket lost after the hub sent the call is still a failed attempt that follows the step's `retry`, as ADR 0076 says. The step may already have committed or pushed.

The factory's needs_human call is tried again whatever made it fail, with the same backoff and no ceiling on tries. The hub drops it once the run no longer needs a human or has stopped again since. It backs off outside the instance's queue, so a step queued after it does not wait for it. When the socket drops after the call's tool finished, the retry can post a second comment, and that is accepted.

On 2026-10-10 the hub updated the software coder while a run was at its `answer` step. Both tries of that step failed within 40 ms, one refused as draining and one unable to connect, and the needs_human call failed the same way and was dropped. Nobody heard about the run for two hours.

## Considered options

- **Treat every unreachable error as undelivered.** Rejected. A socket that drops while the frame is sent or after it may follow a step that already ran.
- **Retry every failed step with backoff.** Rejected. Which failures of a step that ran should be retried is a separate question (#529).
