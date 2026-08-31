# Placeholder approvals resume from the event log

Superseded by [ADR 0015](0015-sqlite-checkpoints-resume-approvals-after-restart.md).

Ticket #34's asking hook parks before the graph runs, so the parked turn has no graph state to restore. `Turns.respond` reconstructs `TurnRequest` from the persisted `turn.started` and `approval.requested` events, while `LangGraphRunner` keeps its in-memory checkpointer for session working state. This narrows ADR 0007 for the placeholder seam. Ticket #7 must choose a persistent checkpointer when approvals move inside the graph.
