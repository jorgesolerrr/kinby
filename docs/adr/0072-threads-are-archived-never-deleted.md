# Threads are archived, never deleted

A **thread** leaves the sidebar by being archived. An **archived thread** keeps everything it had: its events in the instance's append-only event log, the **episodes** and **facts** that name it, its turn metrics and usage, the routine `last_run` and notices that point at it, and its turn-runner checkpoint. Archiving is a record in the thread store, the way a rename is, and unarchiving is another one. The thread list page shows archived threads, and starting a turn in one brings it back. There is no delete.

## Considered options

- **Delete a thread's events and everything derived from them.** Rejected. One instance keeps one append-only `events.jsonl`, so deleting a thread means rewriting the log. Episodes require a thread id, facts and statistics carry one, and the memory page links to it, so a delete has to cascade through memory and change statistics after the fact.
- **Delete the thread record only, leaving its events.** Rejected. Every reference would then point at a thread that no longer exists, and the events would still be there, so nothing would really be gone.

## Consequences

- Nothing a user writes in a thread can be removed from the instance, which matches the transcript store never being rewritten. Forgetting a fact leaves a **tombstone** so the agent stops recalling it; erasing a thread's history would be a separate privacy feature, not part of v1.
- Anything that resolves a thread id, such as the memory page's "its thread" link, has to look among archived threads too.
