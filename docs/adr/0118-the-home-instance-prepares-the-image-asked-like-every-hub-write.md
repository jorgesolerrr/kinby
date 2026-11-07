# The home instance prepares the image, asked like every hub write

Amends [ADR 0096](0096-the-home-instance-asks-the-hub-and-the-hub-checks-the-approval.md).

Ticket [#1216](https://github.com/jorgesolerrr/kinby/issues/1216): asked to create a vanilla instance, Kinby read `image.describe`, which the hub refused because the image was not prepared. Kinby still asked the user to approve `instance_create`, and the hub refused that call for the same reason. Kinby then sent the user to the web app, whose onboarding page prepares the image every time it loads. `image.prepare` is a `hub:admin` method, so Kinby had no way to run it.

## Decision

- **`hub:home` gains image preparation.** Kinby holds `image_prepare`, a hub write with no arguments. It asks like every hub write, and the hub runs it only for the approval it saw the user give. It returns the preparation's lifecycle operation, or the one already running.
- **The hub records Kinby as the requester** of a preparation Kinby asked for, as it does for the operations ADR 0096 lists. A preparation that was already running keeps its own requester.
- **`image.describe` still never builds.** It stays a read, and Kinby prepares only through the write.

## Considered options

- **Run `image_prepare` without asking**, since preparing is idempotent and the web app does it unasked. Rejected. A build takes minutes and the hub's resources, and ADR 0096 makes every hub write ask. One exception would need its own rule in the gate and in the hub's check.
- **Let `image.describe` prepare when nothing is prepared.** Rejected. A read that starts a build is not a read, and `hub:read` clients would start builds.
- **Only stop the flow before the approval and send the user to the web app.** Rejected as the whole fix. `create-instance` now stops after any failed read, but the user would still leave the chat for a step the hub can do once they approve it.

## Consequences

- `create-instance` prepares the image when `image.describe` is refused because the image is not prepared, then reads it again. It never raises an approval for `instance_create` after a failed read.
- Kinby holds no other `hub:admin` method.
