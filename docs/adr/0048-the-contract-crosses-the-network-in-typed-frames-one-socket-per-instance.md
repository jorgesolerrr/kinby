# The contract crosses the network in typed frames, one socket per instance

The contract server carries the contract over WebSocket as JSON text frames, one message per frame, each with a `type`. A client sends `call`, `subscribe`, and `cancel`. The server answers with `result`, `error`, `subscribed`, `item`, and `end`. The client picks each `id`, unique within its connection. The frame models live in `kinby.contracts`, so the generated TypeScript covers them.

We chose our own envelope over JSON-RPC 2.0. JSON-RPC has no subscription shape, so we would extend it anyway, and its batching and notifications are features we would have to reject.

The hub serves its own contract at `GET /ws`. Each instance gets its own socket at `GET /instances/{instance_id}/ws`, which the hub relays to that instance's private `/ws` without parsing frames. We rejected one multiplexed hub socket with an instance ID on every frame, because the hub would then have to understand every instance method. With a relay, an instance's contract server is the same code with or without a hub in front of it, and a stopped instance fails the upgrade with a 503.

The server answers every `subscribe` with `subscribed {id, head_sequence}` before the first item. Items up to `head_sequence` are replay and later ones are live. The marker is a frame and never an event, because events are a thread's history and catching up is not history.

Closing a socket cancels its subscriptions and nothing else. Turns and lifecycle operations keep running. A client never retries a unary call that was in flight when the socket dropped. It learns the outcome from replayed events, or from `active_operation_id` on the instance's status. We chose that over idempotency keys.

Each subscription has a bounded queue. On overflow the server ends that subscription with a retryable error, and the client resubscribes from its last sequence. The durable event log makes that lossless.

Lifecycle operation progress is polled through `operation.get`, which lists the operation's steps. A second sequenced log for operations that last minutes and have a handful of steps was not worth its cost.

Decision agreed during [Server slice: the contract over WebSocket and the hub's routes](https://github.com/jorgesolerrr/kinby/issues/217).
