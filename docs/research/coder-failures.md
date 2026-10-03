# Research: why the coder's runs fail

Date: 2026-10-03. Ticket: #528, part of map #516. Question: why do the coder's runs sometimes fail to finish an issue, which failures would a plain retry have fixed, and which needed a human?

## What was examined

Read-only, on the playground box and on GitHub:

- The event logs (`.state/events.jsonl`) of the three hub instances: **cec-agent-coder** (`009d97d0`, watches `pb-cec/CEC-Agent`, log from 2026-09-28 23:20), **Forge** (`1c50cdc0`, `kinby-e2e-sandbox`, from 2026-09-27) and **Anvil** (`6e46798a`, `kinby-e2e-sandbox`, from 2026-09-27).
- The legacy coder at `kinby-hub/coder` (container `kinby-coder`, watches `jorgesolerrr/kinby`, log from 2026-09-11). The ticket did not name it. It has the longest history, so it is reported separately.
- The Claude Code session transcripts in the cec-agent-coder container (`/root/.claude/projects/-instance-workspace`, 58 sessions). The `kinby-coder` container was recreated on 2026-10-02, so its older sessions are gone.
- The `ready-for-agent` and `ready-for-human` label events on the three watched repositories, and the comments the model step posted on every failed issue.
- The pipeline code in `kinby-code-factory` at `d3dfa92`.

A run counts as failed when the code step returned a **pipeline report** with outcome `failed`. A turn counts as failed when it ended in `turn.failed` or never ended. Those are different things: a failed pipeline run ends its turn as `work`, and only the model step that follows can fail the turn.

## Counts

| Instance | Pipeline runs | Opened | Failed | Other failed turns |
|---|---|---|---|---|
| cec-agent-coder | 61 | 50 | 11 | 1 model step |
| Forge | 1 | 0 | 1 | none |
| Anvil | 0 | 0 | 0 | none (70 no-work wakes; container exited 137 on 2026-09-30 with nothing in flight) |
| kinby-coder (legacy) | 125 | 112 | 13 | 1 interrupted, 3 babysit failures, 11 model step |

Failed runs by cause:

| Cause | cec | Forge | kinby-coder | Total |
|---|---|---|---|---|
| Claude ended its turn with a background task still running ("Claude returned an invalid result") | 6 | | 2 | 8 |
| Coding client timeout | | | 6 | 6 |
| Coding client error or non-zero exit | | | 3 | 3 |
| git or gh error | 3 | | 1 (+3 babysit) | 4 (+3) |
| Client stopped without a PR body | 1 | | 1 | 2 |
| Checks still failing after the repair attempt | | 1 | | 1 |
| Work already merged, nothing to commit | 1 | | | 1 |
| Crash or restart mid-run | | | 1 turn | 1 |
| Subscription limit | 0 | 0 | 0 | 0 |
| Budget reached | 0 | 0 | 0 | 0 |

Every one of the 149 `run.delegated` events recorded since 2026-09-25 has outcome `completed`. No run hit a plan-window limit and no turn was refused for budget.

Model step failures, which fail the turn after the code step has finished: 12. Nine came from "Your credit balance is too low to access the Anthropic API" (2026-09-15 to 09-18) and three from "credential validation failed" (2026-09-29, 14:13 to 14:34, on both cec-agent-coder and kinby-coder). In three of them (cec#34, kinby#423, kinby#424) the PR opened and only the summary comment was lost. In the other nine the pipeline had already failed, so the issue or PR got the `ready-for-human` label and no comment saying why.

## The causes

### 1. Claude ends its turn while a background task runs (8 runs)

All eight failed with the same reason, `Claude returned an invalid result`, and each one's `run.delegated` event says `outcome: completed`. The transcripts show what happened. The last assistant message of each failed cec session:

- #97: "The full test suite is still running in the background. I'll commit once it finishes."
- #132: "The full suite is still running; I'll report once it finishes."
- #134: "The suite is still running. I'll commit once it reports."
- #137: "The full `pytest` run is still going in the background. I'll pick up again when it finishes: fill in its result, then commit."
- #138: "The full test suite is still running in the background. I'll commit once it passes."
- #130 committed (`88b3f06`, "The full suite passes: 718 tests"), but it had started a MinIO server with `run_in_background` at 18:11 that was still running when the turn ended.

In the successful session traced the same way (#134's rerun), every background task had finished before the last message. The error text says the last line of stdout was valid JSON but not a `result` event, so Claude Code wrote something after the result; a task still running at the end of the turn is the likely source. `_claude_success` (`clients.py:320-331`) parses only the last non-empty line, finds no `result` there, and raises. `_claude_run` (`clients.py:341`) uses `_claude_final_event`, which searches back for the last `result`, and so it reports the same run as completed. The two disagree about the same stream.

The two kinby-coder cases (#402, #440) have the same signature: the run is reported completed, the reason is "invalid result", and the run lasted 14 minutes. Their transcripts are gone, so the cause there is inferred.

Retry: of the seven issues that were relabeled, six succeeded on the rerun (#97, #132, #134, #137, #402, #440). #130 was closed by hand. #138 was relabeled at 21:44 on 2026-10-03, and that rerun failed before it started (see section 4). Each rerun repeated the whole implementation, 5 to 25 minutes of Opus work, because the pipeline throws the first attempt's branch away.

### 2. Coding client timeouts (6 runs, all kinby-coder)

- #236 twice and #237 twice: "codex exceeded its 900-second limit and was killed" (2026-09-15).
- #252 twice: "claude exceeded its 1800-second limit and was killed" (2026-09-18).

Each issue got a second attempt right after the first, because a queued webhook delivery ran the pipeline again. The second attempts failed the same way. #252 passed hours later after a relabel; #236 and #237 went to a human. A plain retry did not fix these. The limits are now 3600 s to implement and 900 s to fix, and there has been no timeout since 2026-09-18.

### 3. Coding client errors (3 runs, kinby-coder)

- #210: "codex exited with status -9" after 24 minutes. The process was killed from outside while it ran the checks, probably OOM. Not rerun.
- #226: "codex exited with status 1: … apply_patch verification failed … failed to record rollout items: thread … not found". Not rerun.
- #243: Claude ended with `"api_error_status":529`, "API Error: 529 Overloaded". The rerun succeeded (PR #264). A retry fixes this one.

### 4. git and gh errors (4 runs, plus 3 babysit runs)

- cec#14: "could not request reviewer: 'pb-cec' not found". The pipeline asked GitHub to make an organization the reviewer. Needed a code fix: kinby-code-factory #12 merged at 01:01:45, and the rerun at 01:02:55 opened PR #23.
- cec, 2026-09-29: "gh: This issue was deleted (HTTP 410)". An issue in the ready list had been deleted (#55 no longer exists). Nothing to retry.
- cec, 2026-10-03 21:44: "gh exceeded its 60-second limit and was killed", before any issue was selected. This was the relabel of #138.
- kinby-coder, 2026-10-03 21:47: "gh: No server is currently available … (HTTP 503)", before any issue was selected.

The last two failed before an issue was chosen, so no label changed and the hourly schedule retries them on its own.

Babysit: three hourly rounds on PR #248 failed with "fatal: 'agent/237-…' is already used by worktree at '/instance/recovery-237'". A recovery worktree created by hand held the branch. That needed a human.

### 5. The client stopped without a PR body (2 runs)

- cec#55 (10 client turns): "I didn't implement #55. It belongs to a different repository … 'Repo: CEC-Backend, not this repo.'" The ticket was wrong. The client was right to stop.
- kinby#354 (7 client turns, 29 s): "could not read pull request body". The transcript is gone. The issue stayed `ready-for-human`.

Both needed a human.

### 6. Checks failed after the repair attempt (1 run, Forge)

kinby-e2e-sandbox#1: "uv exited with status 2: error: Failed to spawn: `ty` … No such file or directory". The image had no `ty`, so the repair run could not fix it. The same report adds "label update failed: … 'ready-for-human' not found", because the sandbox repository has no such label. Environment problem; needed a human.

### 7. Work already merged (1 run)

cec#62: "No commits between agent/59-… and agent/62-…". PR #75 had merged into the stacked `agent/61` branch, not `main`, so `Closes #62` never fired and the issue kept `ready-for-agent`. The client found nothing to do ("Issue #62 was already implemented and is on `main`, so I made no commit"), and the PR step failed. The same gap made the coder build kinby#440 twice (PR #446 merged into `agent/441-…`, then PR #450) and redo cec#54 and #59 on 2026-09-30.

### 8. Restart mid-run (1 turn)

kinby-coder turn `e156898a` (2026-09-19) called `implement_ready_issue` at 17:14 and ended in `turn.interrupted` at 17:42, with no tool result. Which issue it held is not recorded. A retry fixes this.

## Timeline

| Date | Instance | Issue | Cause |
|---|---|---|---|
| 09-11 | kinby-coder | #210 | codex killed (-9) |
| 09-12 | kinby-coder | #226 | codex exit 1 (apply_patch) |
| 09-15 | kinby-coder | #236 ×2, #237 ×2 | codex 900 s timeout; model step: no credit |
| 09-16 | kinby-coder | PR #248 ×3 | babysit: branch held by a recovery worktree |
| 09-18 | kinby-coder | #252 ×2 | claude 1800 s timeout; model step: no credit |
| 09-19 | kinby-coder | ? | turn interrupted by a restart |
| 09-22 | kinby-coder | #243 | API 529 overloaded |
| 09-27 | kinby-coder | #354 | no PR body |
| 09-28 | kinby-coder | #402 | invalid result |
| 09-29 | cec | #14 | gh reviewer is an organization |
| 09-29 | cec, kinby-coder | #34, #423, #424 | model step: credential validation (PRs opened) |
| 09-29 | cec | #55, deleted issue | wrong-repo ticket; HTTP 410 |
| 09-29 | kinby-coder | #440 | invalid result |
| 09-30 | cec | #62 | no commits (already merged into a stacked branch) |
| 09-30 | cec | #97 | invalid result |
| 09-30 | Forge | sandbox#1 | checks after repair (`ty` missing) |
| 10-02 | cec | #130, #132 | invalid result |
| 10-03 | cec | #134, #137, #138 | invalid result |
| 10-03 | cec, kinby-coder | none selected | gh 60 s timeout; gh HTTP 503 |

## What a retry would have fixed

| Would a plain retry fix it? | Cases |
|---|---|
| Yes | 8 invalid result (6 confirmed by reruns), #243 (529), 2 transient gh errors, the interrupted turn: **12** |
| No: same failure on retry | 6 timeouts (three back-to-back pairs failed alike) |
| No: needed a code, ticket or environment change | cec#14 (code fix), #55 and #354 (ticket or client stop), cec#62 (already merged), sandbox#1 (`ty` missing), babysit #248 (manual worktree, 3 rounds), the deleted issue: **7 cases** |
| Unclear | #210 (killed), #226 (apply_patch): **2** |

Since 2026-09-25 the picture is simpler. Of the 16 failed runs on cec-agent-coder, Forge and kinby-coder in that period, 8 are the background-task bug and 2 are transient gh errors. One retry would have fixed 10 of 16, but fixing the bug removes the main cause.

## Bugs found in the pipeline

1. **The result parser reads only the last line.** `clients.py:322-328`, `_claude_success`, takes `lines[-1]`. `_claude_final_event` (`clients.py:334`), which the run report uses, searches for the last `result` event. Switching `_claude_success` to `_claude_final_event` would have opened #130's PR, which was committed and checked. On its own it does not fix the other seven, where the client stopped before it committed.
2. **The implement path never checks that the work was committed.** `verify_committed_workspace` (`pull_request.py:105`) runs only in babysit (`babysit.py:349`). `implement_ready_issue` goes straight from the client to `open_pull_request` (`pipeline.py:237`), which pushes whatever HEAD is. Uncommitted work turns into "No commits between" (as in cec#62) or a PR without the uncommitted part. The fix for cause 1 needs this check, plus a resumed session ("finish and commit") when the client ends with work in flight.
3. **A failure throws away committed work.** `pipeline.py:279-280` calls `discard_branch_for_report`, which runs `git branch -D` (`pull_request.py:127`). #130 lost commit `88b3f06` after 24 minutes of work. Every rerun in cause 1 started from nothing.
4. **The failed report hides what the client did.** `implementation` is assigned only when `run_implementation` returns (`pipeline.py:166`), so a parse failure reports `implementation: null`. The model step then tells the issue "No changes were made" (cec#134, #138) or "no implementation … produced" (cec#97) about runs that worked for 5 to 25 minutes, and in one case committed.
5. **A missing label drops the issue out of sight.** `mark_ready_for_human` (`repository.py:428-437`) removes `ready-for-agent` and adds `ready-for-human` in one `gh issue edit`. In kinby-e2e-sandbox the add failed and the remove went through (`unlabeled ready-for-agent` at 21:52:52), so the issue has neither label.
6. **Merged stacked PRs do not count as covering their issue.** `oldest_eligible_issue` (`scan.py:63-70`) skips an issue only when an agent PR names it. Once a stacked PR merges into a non-default branch, its issue stays open and `ready-for-agent`, and the coder builds it again (cec#62, cec#54, #59, kinby#440).
7. **Pipeline failures are invisible to the failure policy.** A `FailedPipelineReport` completes the turn as `work`, so the **failure policy** never counts it and no **routine notice** is sent. The only signals are the label and the model step's comment. When the model step fails too (nine turns with no credit on 09-15 to 09-18), the issue gets `ready-for-human` with no explanation. kinby-code-factory #16 already proposes posting the failure without a model call.

Outside the pipeline: the recap of the interrupted turn `e156898a` failed with "messages: at least one message is required" and was retried for five days (2026-09-19 to 09-24).
