# A turn sends the model earlier turns' large tool results cleared

Spec [#1234](https://github.com/jorgesolerrr/kinby/issues/1234). A thread's history only grew. On the home instance, a thread's first model call reached 211k tokens because of tool results from earlier turns. Most turns start after the prompt cache has expired, so rewriting what they send costs almost nothing in cache. Before each model call, the turn runner replaces every tool result over 1,000 characters from an earlier turn with a stub. The stub names the tool and the result's size, and says the tool can be called again. The model's messages, its tool calls and the current turn's results stay whole. The checkpoint and the thread's events keep every result, and only what the model is sent changes.

## Considered options

- **Clear results within a long turn too.** Rejected for now. It would change the prefix mid-turn and break the cache on every call after it.
- **Rewrite the checkpoint.** Rejected. Clearing at send time can be tuned or undone, and the thread the user sees stays whole.
