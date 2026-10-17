# Dreaming also consolidates episodes about one area into an area fact

Amends [ADR 0074](0074-recall-returns-facts-and-episodes-are-raw-material.md).

Ticket [#591](https://github.com/jorgesolerrr/kinby/issues/591): a **factory run** now writes an **episode** that says where a feature lives in the code and what was decided about it ([ADR 0097](0097-a-factory-run-is-recapped-once-from-a-digest-of-its-coding-sessions.md)). Under ADR 0074 **dreaming** writes a **fact** only for a lesson that repeats, and a search returns episodes only when no fact matches. Knowledge about one area shows up once, then changes, so it would never become a fact, and a later session on the same feature would have to find it again.

## Decision

- **Dreaming consolidates by subject as well as by repeat.** Episodes about the same area, such as a feature of a repository, become one **area fact**: where the area lives, and what was decided about it and why. It cites every episode it was learned from as a **source episode**.
- **A newer episode supersedes it.** When a later episode touches the area, dreaming writes a new area fact that cites the old sources and the new one. The latest fact wins, as for any fact.
- **Repeated lessons are consolidated as before.**

How dreaming finds an area and writes the fact belongs to the dreaming spec, [#108](https://github.com/jorgesolerrr/kinby/issues/108).

## Considered options

- **Keep dreaming to repeated lessons.** Rejected. Area knowledge would stay in episodes, which a search returns only as a fallback, and the next session on that area would read the code again.

## Consequences

- The episodes an area fact cites are never pruned while it is live, so an often-touched area keeps its history.
