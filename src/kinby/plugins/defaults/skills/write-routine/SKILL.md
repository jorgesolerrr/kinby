---
name: write-routine
description: Draft kinby routine files for recurring or manual work.
---

Draft a routine as `routines/<name>/ROUTINE.md` in the instance.
The directory name is the routine name. The body after frontmatter is the prompt.
This skill teaches the format and produces a draft for the user to install.
Validated, gated create, edit, enable, disable, and delete operations belong to #61
and are not available through this skill. One-shot reminders belong to #119.
Do not approximate a one-shot reminder with repeating cron.

Write the work of one firing in the imperative. Keep the schedule in frontmatter;
the stored prompt describes work to do now, not a request to set up a schedule.
For example, draft `routines/daily-summary/ROUTINE.md` as:

```markdown
---
description: Summarize today's notes.
schedule: 0 18 * * *
---
Read today's notes. Summarize decisions and unfinished work in this turn.
```

## Frontmatter

Use one `key: value` per line between `---` delimiters.

| Key | Default | Meaning |
| --- | --- | --- |
| `description` | Required, no default | Non-empty description of the routine. |
| `schedule` | Absent, manual only | Five-field cron in the instance's `[routines] timezone`, which defaults to `UTC`. |
| `enabled` | `true` | Allow scheduled firings. A manual run works even when disabled. |
| `mode` | Instance default | `read-only`, `ask`, `auto`, or `full-access`, clamped to the instance ceiling. |
| `catch_up` | `true` | After downtime, fire a missed schedule at most once. A routine with no run history waits for its next scheduled time. |
| `run` | Absent | Name of a shared code-step tool. Without this key or `run.py`, the model runs directly. |
| `arguments` | `{}` | JSON object passed to the code step, written on one line. |
| `steps` | Inherit instance budget, otherwise unlimited | Positive integer counting node executions. |
| `tokens` | Inherit instance budget, otherwise unlimited | Positive integer limiting input plus output tokens. |
| `seconds` | Inherit instance budget, otherwise unlimited | Positive number limiting active execution time. Approval waiting time does not consume it. |

A routine may lower a per-turn budget and never raise one. A value above the
instance budget produces a warning and the instance limit applies. `steps`
counts node executions: `steps: 7` allows four model calls and three tool rounds.
Daily spending stays in the instance's `[budgets] usd_per_day`, outside routine frontmatter.

## Choose a code step

For a deterministic check or data collection used by one routine, place `run.py`
beside `ROUTINE.md`. Define exactly one public function decorated with `@tool(write=...)`
from `kinby.plugins`. Declare its write flag accurately and accept the keys in
`arguments` as function parameters.

When several routines need the same code, move the tool into the instance's `tools/`
and name the exported tool with `run: <tool-name>`. Remove the routine's `run.py`
when using `run`; declaring both warns and skips the routine.

The code step runs before the model and is excluded from the model's available tools.
Its gate must allow execution under the routine's effective mode. Ask and deny fail
the turn with the gate rule. Model-requested tools keep their usual approval flow.

Returning `None` completes a recorded no-work turn when the check finds nothing new.
Both the main model and the recap model are skipped. Kinby preserves run history
and the deterministic tool trace with zero model usage and cost. Every other result,
including an empty string, reaches the model as data. Daily-budget admission still
applies before the firing starts.

## Choose the frequency

Match the frequency to how often the source changes and how soon the user needs
the result. Prefer push when the source supports it; incoming pushes are signals.
The signal receiver is separate work, not a capability this skill installs.
A `run` line with a minutes-level schedule is a smell when the source can push.
Use polling as a fallback for sources without push. Kinby imposes no hard minimum
interval or enabled-routine cap. Per-turn and daily budgets still apply.

Use `catch_up: false` when late work would be unhelpful, such as a morning greeting
after lunch. Omit `schedule` for work the user runs only by hand.

## Inspect failures and recover

Use `kinby routine list` to inspect run history and durable notices. Kinby reports
the first failure in a streak. At ten consecutive failed firings, kinby writes
`enabled: false` and reports the failure reason. Intermediate failures remain in history.
Code-step errors, model errors, and exhausted per-turn budgets count as failures.
Successful work resets the count. No work, interruption, and daily-budget refusal
neither increment nor reset it. An approval waiting for an answer is not a failure.

After fixing the cause, use `kinby routine run <name>` to check a disabled routine.
A successful manual run resets the failure count; a no-work result does not.
A manual run never re-enables the routine. To resume scheduled firings, have the
user explicitly set `enabled: true` in `ROUTINE.md`.
