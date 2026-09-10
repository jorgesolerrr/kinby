# The coder delegates development through a code step

Ticket #181 makes the coder instance an orchestrator. Its routine's **code step** owns the **delegated pipeline**, using Codex as the implementing **coding client** and Claude Code as the independent reviewing client. Sonnet 5 receives only the resulting **pipeline report**, comments on its issue, and supplies the recap. This keeps repository navigation, editing, tests, and **review rounds** on the user's existing subscriptions while deterministic code owns limits and GitHub state.

## Authentication reading

[Claude Code authentication](https://code.claude.com/docs/en/iam) documents `claude setup-token` as a one-year subscription token for CI pipelines and scripts where a browser login is unavailable, including container use. The [Codex authentication guide](https://developers.openai.com/codex/auth) documents seeding a ChatGPT login into private runners and warns against public repositories. We read that warning as credential-exposure guidance, so it does not apply to a private login volume in the user's local container. If OpenAI disallows that use, the pipeline switches Codex to an API key instead of changing its boundary.

## Git rules

The code step creates branches named `agent/<issue>-<slug>` from the issue's stack base. It never pushes to the default branch, never force-pushes, and does not rebase until a later babysitting process can repair the rest of a stack. These rules live in the pipeline code because its Git calls do not pass through the model tool gate or its denylist.

## Rejected options

- **D2, keep coding in the kinby model.** Rejected because the first run spent most of its API cost on repository work that the user's coding subscriptions already cover.
- **D3, use API keys as the primary client credentials.** Rejected because usage-based billing defeats the delegation goal. API authentication remains the Codex fallback if subscription login stops being an allowed automation path.
- **D4, have Sonnet drive the coding clients through model tool calls.** Rejected because a prompt cannot enforce process limits, branch selection, labels, or review rounds as reliably as the routine's deterministic code step.
- **D12, let each coding client choose its branch and push target.** Rejected because an **agent PR** needs a recognizable branch and the pipeline must prevent direct or destructive updates to shared history.
- **D14, rebase an open stack during this pipeline.** Rejected until babysitting exists because changing a lower branch requires coordinated repairs to every **agent PR** above it.
