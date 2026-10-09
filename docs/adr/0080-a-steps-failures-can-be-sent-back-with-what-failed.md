# A step's failures can be sent back with what failed

Amends [ADR 0075](0075-factories-are-hub-run-and-own-their-runs-state.md).

Until now only a clean step could send work back, through one of its declared outcomes. A failed step was retried and then stopped the run as needs human, and its output stayed in the attempt's summary, where no later step could read it. So when the software factory's checks failed, the run stopped for a human, even though rerunning the same commands could never fix it. The repair the factory needed was a coding client fixing what the checks reported.

A step can now declare where its failures go, with a cap, the same way an outcome sends work back. A failed attempt then sends the work back there, carrying what failed as a value: the failing command and the tail of its output. That is a **check repair**. Once the cap runs out, the run is needs human, as before.

The software factory sends a failed `checks` back to `fix`, at most twice. `fix` resumes `implement`'s session. It reruns the failing command for the full output and fixes the root cause.

`fix` comes after `answer`: implement, answer, fix, checks. When work goes back to a step, the run drops every value recorded at that step and after it. If `fix` came before `answer`, a check repair during babysitting would drop the replies, the feedback and the pull request number that `answer` holds. The fix round would then lose its replies, and `publish` would try to open a second pull request.

## Consequences

- A failing gate gets a coding client's attention instead of a human's, the way the `kinby-code-factory` package's one repair session did. Here it is declared in the factory file and capped by the hub.
- A failed attempt can now record a value, and until now only a clean one could.
- Rerunning the failing command costs the repair one command run, and in exchange the run's data stays small.
