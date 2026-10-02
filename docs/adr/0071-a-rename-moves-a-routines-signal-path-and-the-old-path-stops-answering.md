# A rename moves a routine's signal path, and the old path stops answering

A **routine**'s name is its directory, and the **receiver** answers `/signals/<routine>` by loading the routine of that name. The hub's `/instances/{id}/signals/{routine}` and the **signal alias** forward the same name. `routine.rename` moves the directory with one `os.rename`, so the signal path moves with it. After a rename, `/signals/<old>` returns the same 404 as an unknown routine. No redirect, no alias. The Rename dialog names both paths and says the old one stops answering, so the user knows to update every webhook registered against it.

Nothing else carries over either. The failure streak and failure notices fold from events by routine name, so the new name starts without them, the same way re-enabling a routine resets its streak. Instance statistics keep the turns under the old name, which the Origin tab shows as "removed or renamed". The scheduler arms the new name from the rename on, and since it has no last run under that name, catch-up does not fire it again. A rename is refused while deliveries are pending, so no queued delivery is left under a name that no longer exists.

## Considered options

- **Keep answering the old path, as a redirect or an alias to the new name.** Rejected. The receiver would need a record of past names outside the routine's directory, and the directory stops being the routine's whole source of truth. A later routine created under the old name would also collide with the alias.
- **Move the failure streak, notices and pending deliveries to the new name.** Rejected. Each is folded from the event log by name, so moving them means rewriting events or mapping names, for a case the user can avoid by waiting for the deliveries to run.

## Consequences

- Renaming a routine with a signal breaks every webhook that still calls the old path until the user updates it.
- The config change log records a rename as two changes at the same time: the old directory with every file removed and no hash, and the new one with every file added.
- Only the app can rename. The agent has no rename tool.
