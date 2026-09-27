# A stop drains only an instance that can answer

This amends [ADR 0050](0050-a-stop-drains-the-instance-then-takes-the-container-down.md). A container that crash-loops could not be stopped. Docker reported it restarting, the hub had no address to probe, and the stop failed before it reached `docker stop`. A container that had just exited counted as already stopped, so the hub never called `docker stop` and the `unless-stopped` policy brought it back.

A container that is not running has no process to drain. A stop, a force stop, a recreation, an update and a removal all skip the probe and the drain for it and call `docker stop` straight away. An exited container that failed no longer counts as stopped, because only `docker stop` keeps the restart policy from bringing it back. An absent, created or cleanly stopped container still counts as stopped.

A running container whose lifecycle endpoint cannot be reached is terminated by a force stop, the same way a forced instance that never answers its drain already was. A plain stop still fails and leaves it running, and the failure says to force stop. Recreation terminates it without a force flag, because recreation is the way back for a broken container. When the endpoint answers, recreation drains as before. Update and removal still fail on an unreachable running instance.

ADR 0050's rule that Docker's shutdown timeout never replaces the drain still holds for an instance that answers.

Decision agreed during [Stop a crash-looping instance](https://github.com/jorgesolerrr/kinby/issues/331), shipped with #353.
