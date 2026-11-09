# Clients show a thread's context size and a turn's cost

Spec [#1263](https://github.com/jorgesolerrr/kinby/issues/1263). The thread view showed a turn's token usage: input plus output added over every model call. Each call resends the whole context, so that sum counts the same context once per call. It read as the size of the context and as the cost, and it was neither. On the #1262 probe a turn of 998k input tokens had a context of about 38k, and 88% of the input was cache reads billed at about a tenth of the input price.

Clients now show two numbers. A thread shows its context size, the tokens its latest model call sent by the provider's own count, as a bar that fills toward the compaction trigger. A finished turn shows its cost on API use, and its largest context size on a subscription, where nothing is priced. Token usage stays on the stats page and in `kinby usage` under its own name, and the turn's token budget keeps measuring it.

A breakdown says what fills the context. It measures each category in characters and scales the rows to add up to the provider's count for the latest call. It is computed when asked for and stored nowhere.

## Considered options

- **Count each category with the provider's count endpoint.** Rejected. It takes several requests per refresh, and it is exact for one provider only.
- **Estimate the total at four characters per token.** Rejected. The provider already reports the real total on every model call.
- **Fill the bar toward the model's window.** Rejected while compaction is on. With a 100,000-token trigger on a model with a 1M window, the bar would never pass 10%.
- **Require a window for every model.** Rejected. It would break manifests that price their own model. A model without a window shows its context size alone.
- **Show the agent its context size.** Rejected. It would change the prompt on every call and lose the cache.
