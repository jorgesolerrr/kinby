# A compaction is a thread event, applied when the model is sent the thread

Spec [#1263](https://github.com/jorgesolerrr/kinby/issues/1263). A thread's history only grew, and a thread that overflowed the model's window failed its turn. Models also answer worse as their input grows, well before the window is full. A compaction writes a summary of the older history with the main model and records it as a thread event that marks where it fell. Before each model call, the turn runner sends the summary in place of the messages before that mark and keeps the newest turns whole, up to about 20,000 tokens. The checkpoint and the earlier events keep every message, so the user still sees the whole thread.

A compaction runs at the start of a turn whose thread has passed the compaction trigger, an instance setting of 100,000 tokens by default. It also runs when the user asks for it. Inside a turn it runs only when the next model call would not fit the window minus a 16,000-token reserve. Most turns start after the prompt cache has expired, so compacting at a turn's start costs almost nothing in cache.

## Considered options

- **Rewrite the checkpoint.** Rejected, for the reason ADR 0122 gave for cleared results. Applying the summary at send time can be tuned or undone, and the thread the user sees stays whole.
- **Compact right after the turn that crossed the trigger.** Rejected. A thread that is never resumed would pay for a summary nobody uses.
- **Compact inside a turn at the trigger.** Rejected. It rewrites the prefix mid-turn. A cache write costs 1.25 of the input price and a read about 0.1, so the rewrite costs more than the reads it saves. The same arithmetic keeps mid-turn clearing of tool results rejected.
- **Use the recap's episodes as the summary.** Rejected. An episode is written for memory under the instance's recap prompt, and a trace-only recap policy writes none. A summary has to carry the open work.
- **Use the provider's own compaction.** Rejected. It exists for some providers only, and its result is not readable in the thread.
- **Ship an effective window per model.** Rejected. No measurement gives a threshold for agents, so one setting in tokens says what kinby knows.
