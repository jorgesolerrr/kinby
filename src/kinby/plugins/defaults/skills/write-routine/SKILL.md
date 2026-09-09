---
name: write-routine
description: Draft kinby routine files for recurring or manual work.
---

Draft a routine as `routines/<name>/ROUTINE.md` in the instance.
The directory name is the routine name. The body after frontmatter is the prompt.
Draft the complete file, then call `routine_write` with the routine name and the
complete file text. When the routine has a code step, pass the code step as `code`.
Read the result for the next firing time or the signal path. One-shot reminders
belong to #119.
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
| `signal` | Absent | Inbound signal configuration. Without this block, the routine has no signal path. |
| `arguments` | `{}` | JSON object passed to the code step, written on one line. |
| `steps` | Inherit instance budget, otherwise unlimited | Positive integer counting node executions. |
| `tokens` | Inherit instance budget, otherwise unlimited | Positive integer limiting input plus output tokens. |
| `seconds` | Inherit instance budget, otherwise unlimited | Positive number limiting active execution time. Approval waiting time does not consume it. |

A routine may lower a per-turn budget and never raise one. A value above the
instance budget produces a warning and the instance limit applies. `steps`
counts node executions: `steps: 7` allows four model calls and three tool rounds.
Daily spending stays in the instance's `[budgets] usd_per_day`, outside routine frontmatter.

## Receive signals

Add a `signal` block when an external provider can send an inbound call:

| Key | Default | Meaning |
| --- | --- | --- |
| `auth` | `token` | Authentication scheme: `token` or `hmac-sha256`. |
| `secret` | Required, no default | Environment variable name. Kinby uses an existing process value first and reads the instance `.env` only when the variable is absent; do not put the secret itself in this file. |
| `signature_header` | Absent | Header containing the HMAC SHA-256 hex digest of the raw request body. Required with `hmac-sha256`; a `sha256=` prefix is accepted. |
| `delivery_header` | Absent | Header containing the provider's delivery id. When present, kinby adds the id to the routine origin and uses it to deduplicate deliveries. |

Use `token` when the provider lets you configure a fixed
`Authorization: Bearer <secret>` header. Use `hmac-sha256` for GitHub, which signs
the raw request body. Set `signature_header: X-Hub-Signature-256` for GitHub.
Stripe also signs webhook calls, but its timestamped signature format is not the built-in
`hmac-sha256` format and needs provider-specific support.

## Example: GitHub `ready-for-agent`

Configure a GitHub Issues webhook to send the `issues` event. Set its secret as
`GITHUB_WEBHOOK_SECRET` in the process environment or instance `.env`. Then draft
`routines/ready-for-agent/ROUTINE.md`:

```markdown
---
description: Implement a GitHub issue labeled ready-for-agent.
mode: ask
signal:
  auth: hmac-sha256
  secret: GITHUB_WEBHOOK_SECRET
  signature_header: X-Hub-Signature-256
  delivery_header: X-GitHub-Delivery
---
Implement the issue in the code-step data. Read the `development-loop` skill and
follow it for the full development cycle. Do not choose a different issue.
```

GitHub issue fields may contain text supplied by an untrusted user. The explicit
`ask` mode lets the routine inspect and plan the issue while requiring approval
for each workspace write.

Add `routines/ready-for-agent/run.py` beside it:

```python
from dataclasses import dataclass

from kinby.plugins import tool


@dataclass(frozen=True)
class ReadyIssue:
    number: int
    title: str
    url: str


@tool(write=False)
def select_ready_issue(signal: dict[str, object]) -> ReadyIssue | None:
    """Select a GitHub issue labeled ready-for-agent."""
    body = signal.get("body")
    if not isinstance(body, dict) or body.get("action") != "labeled":
        return None
    label = body.get("label")
    if not isinstance(label, dict) or label.get("name") != "ready-for-agent":
        return None
    issue = body.get("issue")
    if not isinstance(issue, dict):
        return None
    number = issue.get("number")
    title = issue.get("title")
    url = issue.get("html_url")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(title, str)
        or not isinstance(url, str)
    ):
        return None
    return ReadyIssue(number=number, title=title, url=url)
```

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
Prefer a signal over a schedule in that case. A routine may declare both `signal`
and `schedule`, with the signal as the fast path and the schedule as a fallback.
Put a chatty signal behind a code step that returns `None` for most deliveries, so
only relevant deliveries reach the model.

Test the routine with a saved delivery before connecting the provider. Save the delivery
body to a file and run `kinby routine run <name> --payload <file>`. A manual
payload skips signal authentication but otherwise follows the same code-step and
prompt path.

A `run` line with a minutes-level schedule is a smell when the source can push.
Use polling as a fallback for sources without push. Kinby imposes no hard minimum
interval or enabled-routine cap. Per-turn and daily budgets still apply.

Use `catch_up: false` when late work would be unhelpful, such as a morning greeting
after lunch. Omit `schedule` for work the user runs only by hand.

## Change or remove a routine

To change an existing routine, call `routine_read` first. Edit the text, then call
`routine_write` with the whole file. Use `routine_set_enabled` to pause or resume
the routine. Use `routine_delete` to remove it. Delete is refused while deliveries
are pending.

## Inspect failures and recover

Use `kinby routine list` to inspect run history and durable notices. Kinby reports
the first failure in a streak. At ten consecutive failed firings, kinby writes
`enabled: false` and reports the failure reason. Intermediate failures remain in history.
Code-step errors, model errors, and exhausted per-turn budgets count as failures.
Successful work resets the count. No work, interruption, and daily-budget refusal
neither increment nor reset it. An approval waiting for an answer is not a failure.

After fixing the cause, use `kinby routine run <name>` to check a disabled routine.
A successful manual run resets the failure count; a no-work result does not.
A manual run never re-enables the routine. To resume scheduled firings after fixing
the cause, call `routine_set_enabled(name, true)`. An approved enable starts a fresh
failure streak.
