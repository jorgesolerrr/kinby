# Routine failure policy is marked in the event log

Ticket #113 derives failure counts from each routine turn's first closing event. The scheduler records `routine.failure.handled` on that turn after applying the failure policy, including any first-failure or disable notice. This keeps notices durable and prevents a restart from repeating an already handled disable action after the user re-enables the file.

A separate runs file would duplicate the canonical history. Inferring whether a failure was handled from the current enabled flag would confuse an automatic disable with a later user edit. Successful work resets the count; no work and interruption leave it unchanged. Daily-budget refusal occurs before `turn.started`, so it contributes no firing.
