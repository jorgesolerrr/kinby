# A start recreates a stopped instance whose secrets were replaced

Ticket #408 lets the user set secrets from the setup card of a stopped instance and expects them to apply when it starts. A container reads its secrets once, when it is created, so a plain start would run the old ones. ADR 0051 made recreation the only operation that applies replaced **instance secrets**. This amends it: `instance.start` on a stopped container whose secrets were replaced recreates the container first, through the same revalidate, remove, and create steps as `instance.recreate`, then starts it. A running instance still applies them only through `instance.recreate`, and recovery still starts no replacement.

To tell, the hub labels each container it creates with `kinby.secrets`, the sha256 of the environment it was created with. A **recreate reason** of `secrets` is a label that differs from the digest of the instance's `.env`. `instance.status` lists it beside `package_config`, which the running instance reports from its own `instance.probe` over the control socket, so the notice in the web app survives a reload without the hub storing anything.

## Considered options

- **Record replaced secrets in the registry and clear the flag on recreate.** Rejected. A secret written by hand, or a recreate the hub did not finish, would leave the flag wrong. The label is written by the same call that creates the container, and `.env` is where the secrets are.
- **Leave the start alone and show the notice on the stopped instance too.** Rejected. It asks for two operations where the user asked for one, and a stopped container can be recreated without a drain.

## Consequences

- A container created before the label has none, and reads as holding the current secrets. One recreate labels it.
- The label holds a hash, never a value. Anyone who can read it can already read the container's environment.
- A start on a stopped instance with replaced secrets reports the `recreate` step and the recreation's own steps in place of `start` alone.
