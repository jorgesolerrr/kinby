# Hub secrets are linked and written into each instance's secrets

Ticket #711: every coder asks for the same five secrets, and rotating the GitHub token means replacing it on each coder. The hub now keeps **hub secrets**, named values set once. An instance that takes one for a secret setup field stays linked to it: the instance registry records the field and the hub secret's name, and the hub writes the value into the instance's `.env`, as it writes any instance secret. Replacing a hub secret rewrites the linked `.env` files, and ADR 0070's `kinby.secrets` label turns each rewrite into a `secrets` recreate reason with no new state. The hub offers to recreate the running instances at once. Stopped ones recreate when they start.

A replacement rewrites a linked field only while the instance's `.env` still holds the hub secret's previous value. A field that holds anything else was changed on purpose, by hand or through `instance.secrets.set`, so the hub unlinks it and names the instance instead of overwriting it. Setting a linked field through the hub unlinks it too. Deleting a hub secret unlinks every instance and leaves their values in place.

## Considered options

- **Copy the value at creation and record nothing.** Rejected. Rotating a token would still mean visiting every instance, which is half of what #711 asks to fix.
- **Have the container read the hub secret from the hub when it starts.** Rejected. A container would need a channel to the hub's secrets, and every start would depend on a running hub, while today an instance runs without its hub. The `.env` stays the one place an instance's secrets are.
- **Always overwrite linked fields on replacement.** Rejected. A value set by hand after linking would be lost without a word. The hub already holds the previous value, so the comparison costs nothing.

## Consequences

- The instance registry holds hub secret names, never values. The hub secrets file is the hub's only new store of values.
- A hand edit to a linked `.env` field silently becomes an unlink at the next replacement, and the replacement result names it.
- Deleting the hub's copy revokes nothing. Revoking a token happens at its provider.
