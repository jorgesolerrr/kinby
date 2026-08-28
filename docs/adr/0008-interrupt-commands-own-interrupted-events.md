# Interrupt commands own interrupted events

`Turns.interrupt` appends `turn.interrupted` and makes the thread available before it returns. The runner may suppress task cancellation during cleanup, so deriving this event from `CancelledError` could hang the command, emit `turn.completed`, or record a user interruption during process shutdown. Cleanup may overlap the next turn, but the interrupted runner cannot append more events. Waiting for that runner would let it block the next turn. Clients such as the REPL translate user input into `thread.turn.interrupt`; they do not write the event.
