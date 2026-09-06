# Routine code steps require gate allow

Ticket #112 runs a routine's code step inside the turn runner, before the model, under the routine's permission mode clamped to the instance ceiling. The step requires an allow from the gate. Ask and deny fail the turn with the gate rule because the author's routine mode supplies the permission decision for this deterministic step.

The runner records the tool call and result, and excludes the step from the model's tools. Model-requested tools retain the existing approval flow. The runner reloads routine files when preparing a resumed turn but does not repeat its completed code step.
