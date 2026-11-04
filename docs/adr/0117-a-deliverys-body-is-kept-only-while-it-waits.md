# A delivery's body is kept only while it waits

A GitHub webhook body is about 19 KB, and each scan stored it three times: in `signal.received`, verbatim as the turn's message (which the model never sees), and parsed as the code step's tool call arguments. After its turn starts, nothing in kinby reads the body again. The scan reads a handful of fields, the hub matches factory waits on its own in-memory copy, and a model routine's prompt is already kept in the turn runner's checkpoint. The body is input, not history. So the body waits with its **delivery** until the delivery's turn starts, and is then dropped. The **transcript store** records the arrival: the provider's delivery id, the routine, the size, a hash of the body, and a few named headers. The turn's message and the code step's tool call refer to the delivery instead of repeating it.

## Considered options

- **Keep the body once, forever.** Rejected. It costs about 19 KB per scan that usually finds nothing to do, and it buys only debugging after the fact.
- **Trim the body at receipt to the fields routines read.** Rejected. kinby would have to know what every routine reads, and that knowledge goes stale.

## Consequences

- A scan that misbehaved cannot be inspected from its body afterwards. A setting that keeps bodies for some days can be added if debugging needs it.
- The transcript store is still never rewritten: the body never enters it.
