# The hub aggregates usage live and stores none

The hub's `stats.summary` calls `stats.get` on every running instance over its `/control` route, in parallel with a short timeout, and sums the answers. It returns each instance's buckets, the totals per **usage source**, the latest limited run per source, and the instances it skipped or could not reach. The hub keeps no usage in its SQLite registry.

Each instance's event log stays the only record of its usage ([ADR 0018](0018-statistics-and-evals-derive-from-the-event-log.md)), so there is no copy to drift. The cost is that a stopped instance drops out of the totals. A stopped instance spends nothing now, so the 5-hour view stays right, but a 7-day total can undercount until the instance starts again. The reply always names the missing instances, so a partial total never looks complete. If that gap starts to matter, the hub can save each instance's last summary when it drains or stops it.

The sum assumes one account per subscription source across instances. Kinby is single-user, so every instance's Claude login is the same plan and draws from the same **plan window**. A delegated run carries no account id. If two accounts per source ever happen, the run gains an account field and the hub groups by it.

Decision agreed during [Usage accounting across the API and the two subscriptions](https://github.com/jorgesolerrr/kinby/issues/219).
