# Shipped bash deny patterns always apply

Ticket #305 settled the permissions section of the config panel. Until now, a `bash.deny` list in `permissions.toml` replaced the patterns kinby ships (`SHIPPED_BASH_DENY`), so one hand-written list could silently drop the protection against `rm -rf` of the instance, `git reset --hard`, and force pushes. The panel shows the shipped patterns as locked, and that has to be true for the file as well as for the app. So the shipped deny patterns now always apply, and `bash.deny` in the file holds only the instance's own patterns, which add to them. This amends ADR 0014, where the denylist is a tripwire enforced in every mode. It stays a tripwire.

## Considered options

- **Keep replacement and refuse a `permissions.set` that drops a shipped pattern.** Rejected. The app would protect the patterns, but a hand edit on disk could still remove them, so "locked" would mean one thing in the app and another in the file.

## Consequences

- An existing file that repeats the shipped patterns keeps working. The gate merges both lists and drops duplicates.
- A user can no longer turn off a shipped pattern. If one gets in the way, the fix is a kinby change, not an instance setting.
- `permissions.get` returns each deny pattern with whether it is shipped, and `permissions.set` takes only the instance's own patterns.
