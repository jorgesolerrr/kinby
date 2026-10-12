# A failed check on the head blocks merge-ready

Amends [ADR 0078](0078-the-software-factory-babysits-its-pull-requests-until-merge-ready.md), [ADR 0080](0080-a-steps-failures-can-be-sent-back-with-what-failed.md) and [ADR 0082](0082-babysitting-ends-on-a-quiet-merged-or-closed-pull-request.md).

`assess` waited while a check run on the head was still running, and never read how a finished one ended. A pull request whose CI failed got the `merge-ready` label and a review request for the maintainer if a trusted reviewer had reviewed its head. After a quiet wait it got them with no review at all.

A failed check on the head now blocks merge-ready. `assess` still handles review threads first. Answerable threads start a fix round whatever the checks say, because the round pushes a new head and CI runs again on it. With no thread to handle, it reads the conclusion of every finished check run:

- `failure` and `timed_out` are something a coding session can repair. `assess` returns the outcome `checks-failed` and records `failure`, which names each failed check with its conclusion and link, and `pr`. The software factory sends `checks-failed` back to `answer`, at most twice, as a **check repair**. `answer` finds no threads and changes nothing. `fix` reads the checks' logs with `gh` and fixes the cause, then `checks` runs and `publish` pushes the repair to the open pull request.
- `cancelled`, `action_required` and `startup_failure` are not. The run goes back to wait.
- `success`, `neutral`, `skipped` and `stale` do not block merge-ready.

A trusted review app's check run is exempt. Its failure means a review with findings, and those arrive as threads.

Neither `checks-failed` nor a wait on a check labels the pull request, comments on it or asks for a review. After the cap, the next `checks-failed` leaves the run needs human, and the failed checks are in its summary.

`checks-failed` is an outcome of `assess`, not a `failed:` edge. A `failed:` edge would send every failure of `assess` to `fix`, including feedback from an untrusted author, which must stay needs human.

`checks-failed` goes back to `answer`, not straight to `fix`, for the reason ADR 0080 put `answer` before `fix`. The values a send-back carries sit at the step the work goes back to. When the repair's own `checks` fail, they send the work back to `fix`, which drops everything recorded at `fix` and after. Carried to `fix`, `pr` would be lost, and `publish` would try to open a second pull request. Carried to `answer`, `failure` and `pr` survive. Going back to `answer` also drops the replies of an earlier fix round, so `publish` posts none of them again.

## Consequences

- A code step's tool can now give its result a summary through `HookResult`, so the failed checks show in the attempt and in the needs-human report.
- A check repair runs `answer` first, which costs one short turn of the coding client.
- The run engine counts send-backs by the step they leave and the step they reach, so fix rounds and check repairs from `assess` share one count. A check repair needs fewer than two earlier rounds, and a fix round fewer than three. Separate counts would need the engine to count by outcome.
- `assess` reads check runs only. A repository that reports CI through legacy commit statuses still gets merge-ready with a failed status.
- Every finished check run counts, required or not. Branch protection does not decide which checks block.
