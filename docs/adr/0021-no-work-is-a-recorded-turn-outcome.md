# No work is a recorded turn outcome

Ticket #112 records a routine code step returning `None` as `turn.completed` with the explicit `no-work` outcome. This supersedes ADR 0019's statement that no turn starts, keeping the event log as the only run history without treating empty model responses or zero-token work as no work.

Recap writes the deterministic tool trace and a zero-usage `memory.recapped` marker with no model attribution. The trace ID derives from the turn so recovery after writing the trace but before its marker replaces the same node. Pricing treats the explicit no-work outcome as zero cost, including when a configured but unused model is unpriced. Daily-budget admission still runs before the turn starts.
