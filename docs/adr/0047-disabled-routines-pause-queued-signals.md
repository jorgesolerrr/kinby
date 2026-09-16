# Disabled routines pause queued signals

Disabling a routine must stop automatic work that has already reached its queue,
including babysitting deliveries accepted before deployment. The scheduler leaves
those deliveries pending while the routine is disabled. Other enabled routines
continue to run. Re-enabling the routine makes its queued signals eligible again.

Explicit manual runs, including runs with a payload, still work while disabled.
This replaces ADR 0025's rule that accepted signals fire even after disablement.
The event log keeps every receipt; disabling does not delete queued work.
