# Vercel agent-browser for kinby's browser automation

Research for [#922](https://github.com/jorgesolerrr/kinby/issues/922), feeding [#161](https://github.com/jorgesolerrr/kinby/issues/161). It compares against the recommendation in [#920](https://github.com/jorgesolerrr/kinby/issues/920) (`docs/research/web-search-and-browser.md` on branch `research/web-search-and-browser`): Playwright for Python behind kinby's own tools.

Sources are pinned to [vercel-labs/agent-browser at `372ac5c`](https://github.com/vercel-labs/agent-browser/tree/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb) (2026-10-23), release [v0.39.2](https://github.com/vercel-labs/agent-browser/releases/tag/v0.39.2) (2026-10-22). Read and measured on 2026-10-25. "README" below means [`README.md` at that commit](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md).

## Short answer

**Keep Playwright for Python, and borrow parts of agent-browser's design.**

agent-browser is a good tool for coding agents that have a shell. Its model is the one #920 recommends: an accessibility snapshot with refs, then one command per action. But for kinby it adds no capability that Playwright for Python lacks, costs about the same in the image, and costs more in memory, because it runs full Chrome rather than the headless shell. It would also put a Rust daemon and a CLI-over-subprocess layer between kinby's tools and the browser. The parts worth copying are small: the compact ref format with an interactive-only filter, refs that survive re-renders, clicks that fail when something covers the target, an output cap, action categories that map onto the gate's read/write split, and a viewport stream over WebSocket that takes mouse and keyboard input for human takeover.

## What it is

- A "browser automation CLI for AI agents", written in Rust, Apache-2.0 ([repo](https://github.com/vercel-labs/agent-browser), [LICENSE](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/LICENSE)). A CLI talks to a background daemon that drives Chrome over CDP directly. No Node.js or Playwright is needed at runtime ([README, Architecture](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#architecture)).
- It is called three ways: the CLI, an MCP server over stdio (`agent-browser mcp`, tool profiles `core`, `network`, `state` and others), and an optional `chat` command that runs its own LLM loop through the Vercel AI Gateway ([README, MCP Server](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#mcp-server), [AI Chat](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#ai-chat)). There is no Python library; the repo's only Python file is an eval script.
- Maturity: repo created 2026-01-11, 114 releases since 2026-01-23 (about three a week; eight between 2026-09-08 and 2026-10-22), still 0.x, about 44.6k stars, 703 open issues and PRs, 30+ contributors (GitHub API, 2026-10-25). It is a Vercel Labs product. The pace means frequent changes; how often they break the CLI was not checked (**unverified**).

## How the model acts

- **Commands.** `open <url>`, `snapshot`, `click @e2`, `fill @e3 "text"`, `get text @e1`, `screenshot`, `read`, `close`, plus waits, tabs, frames, network, cookies and storage ([README, Core Commands](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#core-commands)). CSS, text, XPath and semantic locators (`find role button click --name Submit`) also work.
- **Snapshot format.** A YAML-like accessibility tree with refs: `- button "Submit" [ref=e2]`. Options: `-i` interactive elements only, `-c` compact, `-d <n>` depth, `-s <selector>` scope, `--delta` for incremental updates after the first full snapshot ([snapshots doc](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/docs/content/docs/snapshots.mdx), [README, Snapshot Options](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#snapshot-options)). Same-origin iframes are inlined one level deep, and their refs work without switching frames.
- **Refs.** A ref keeps pointing at its DOM node across snapshots while the node exists. If the node was replaced, the action falls back to the one element with the same role and name, and fails without acting when that match is ambiguous. A click fails, rather than reporting success, when the target is hidden, disabled, off-viewport, or covered by another element (the error names the cover, such as a consent banner) ([README, Refs](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#refs-recommended-for-ai)).
- **Output cap.** `--max-output <chars>` truncates page output; `--content-boundaries` wraps page text in nonce delimiters ([README, Security](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#security)).

### Token cost per step (measured)

The same page #920 measured, English Wikipedia "Web browser", in v0.39.2 (characters; tokens at about 4 characters each):

| Output | Characters | About tokens |
|---|---|---|
| `snapshot` (full tree) | 84,865 | 21k |
| `snapshot -s "#mw-content-text" -d 4` | 35,137 | 8.8k |
| `read` (rendered page as text) | 33,497 | 8.4k |
| `snapshot -i` (interactive only) | 20,074 | 5k |
| `snapshot -i -c` | 20,074 | 5k (`-c` saved nothing here) |
| `--max-output 8000 snapshot` | 8,070 | 2k |

For comparison, #920 measured Playwright's `aria_snapshot()` of the same page at 74,707 characters. The interactive-only filter is the useful saving, about a quarter of the full tree. Playwright for Python has no such filter built in; kinby's `snapshot` tool would have to build one (**unverified** how much work that is).

## Visibility to kinby's gate

- **Each CLI or MCP call is one action.** A kinby tool that wraps one command keeps every click a separate tool call, as ADR 0014 needs. The `chat` command and the dashboard's AI chat run their own model loop; kinby would not use them.
- **Two features bundle actions.** `batch` runs several commands in one call, and the docs recommend chaining commands with `&&` in one shell call ([README, Command Chaining](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#command-chaining)). A generic "run agent-browser with these arguments" tool would let the model do that and hide the actions from the gate. kinby would need one typed tool per action, the same as with Playwright.
- **It has its own policy layer.** An `--action-policy` JSON file allows or denies action categories (`navigate`, `click`, `fill`, `eval`, `download`, `upload`, `snapshot`, `get`, `read`, `state` and others), and `--confirm-actions` returns a `confirmation_required` response that an orchestrator approves with `agent-browser confirm <id>` within 60 seconds ([security doc, Action Policy](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/docs/content/docs/security.mdx)). This duplicates kinby's gate. The category split is the useful part: `snapshot`, `get`, `read`, `wait` and `scroll` are reads; `click`, `fill`, `upload`, `download`, `eval` and `state` change things.
- **WebMCP is on by default.** In Chrome launched by agent-browser, pages can advertise tools, and responses announce them ([README, WebMCP](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#webmcp-experimental)). That puts page-supplied text in the model's context. kinby would pass `--no-webmcp`.

## Running headless in `python:3.14-slim` (measured)

Measured in a throwaway container from `python:3.14-slim`, run with `--ipc=host --init`, on 2026-10-25:

- **Binary.** The `agent-browser-linux-x64` release asset is 18 MB ([v0.39.2 assets](https://github.com/vercel-labs/agent-browser/releases/tag/v0.39.2)); downloading it needs no Node.
- **`install --with-deps` fails as root in the slim image.** It shells out to `sudo apt-get`, and the image has no `sudo`. Installing its printed package list with `apt-get` directly, then `agent-browser install`, works.
- **Size.** System libraries and fonts added 421 MB, and `agent-browser install` downloaded full Chrome for Testing 156 (403 MB in `~/.agent-browser/browsers`). Binary, browser and libraries came to about 823 MB on top of the image. #920 measured 817 MB for Playwright with the headless shell, so the two are about the same.
- **Memory.** With one Wikipedia page open, summed RSS was 1,673 MB over 14 Chrome processes plus the 81 MB daemon. That sum double-counts shared pages; proportional set size (PSS) totalled 670 MB, and the cgroup reported 413 MB anonymous memory. #920's 684 MB was summed RSS for Playwright's headless shell, so on the same measure agent-browser's full Chrome uses more than twice as much. Pointing `--executable-path` at Playwright's headless shell might close the gap (**unverified**; the docs say existing Playwright installs are detected, [README, Requirements](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#requirements)).
- **Sandbox.** It adds `--no-sandbox` automatically when it detects a container or root ([installation doc](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/docs/content/docs/installation.mdx)). The same hardening concern as Playwright applies.
- **Daemon lifecycle.** The daemon starts on the first command and shuts down after one hour idle by default (`--idle-timeout`). The default operation timeout is 25 s, under a 30 s IPC timeout; a page-changing command that times out at the IPC layer returns `outcome_unknown` instead of retrying ([README, Default Timeout](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#default-timeout)). kinby's tools would have to handle that code.

## Sessions, profiles and signed-in state

- `--session <name>` isolates a browser, its cookies, storage and history. `--restore` saves and restores cookies and localStorage under `~/.agent-browser/sessions/`, on close and every 30 s while open; `AGENT_BROWSER_ENCRYPTION_KEY` encrypts the files with AES-256-GCM, and states older than 30 days are deleted by default ([README, Session Persistence](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#session-persistence)).
- `state save <path>` / `--state <path>` load and save a state JSON with cookies and origins, like Playwright's storage state. Whether the files are interchangeable with Playwright's was not tested (**unverified**).
- `--profile <path>` keeps a full persistent profile (IndexedDB, service workers, cache). `--profile <name>` copies the user's own Chrome profile, which does not apply in a container.
- An auth vault (`auth save` / `auth login`) stores credentials encrypted and fills the login form without the model seeing the password. kinby's rule is that the user signs in, not the agent, so this is not needed.
- `--allowed-domains` is refused together with restore or state replay ([README, Security](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#security)), so a signed-in session cannot also use its domain allowlist.

## Human takeover for sign-in or CAPTCHA

- **Viewport stream with input.** Every session starts a WebSocket server that sends JPEG frames and URL updates, and accepts `input_mouse`, `input_keyboard` and `input_touch` events, for "pair browsing" where a human interacts alongside the agent ([README, Streaming](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#streaming-browser-preview)). At 1280x720 a frame is about 54 KB at quality 80, 9 KB at quality 20 and 640x360. This is a CDP screencast, so it works headless with no X server or noVNC.
- **Dashboard.** `agent-browser dashboard start` serves a live viewport, an activity feed and console output on port 4848; behind a reverse proxy it requires an access token and an origin allowlist ([README, Observability Dashboard](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#observability-dashboard)). Whether a human can type into the dashboard viewport, or only into the raw WebSocket, was not checked (**unverified**).
- **Headed fallback.** `--headed` on a Linux box without `DISPLAY` starts a private Xvfb if one is installed ([README, Headed Mode](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#headed-mode)).
- **CAPTCHA.** Solving and stealth come only through third-party plugins or cloud providers (Browserbase, Browserless, Browser Use, Kernel, AgentCore) ([README, Plugin System](https://github.com/vercel-labs/agent-browser/blob/372ac5ce20fab885dbf45ee0c9d510ec8c3b6fdb/README.md#plugin-system)).

The stream protocol is the most useful find for kinby. It shows that takeover needs no noVNC: the hub can relay a CDP screencast and send input back. Playwright for Python exposes CDP sessions, so kinby can build the same thing itself (`Page.startScreencast` plus `Input.dispatchMouseEvent`); that has not been built or measured here (**unverified**).

## How kinby would call it

- **CLI through a tool.** Each kinby tool spawns `agent-browser --session <instance> ... <command>` and parses text or `--json` output. That is one process spawn per action, plus a daemon per instance whose lifecycle kinby must manage (start, idle timeout, `close`).
- **MCP.** `agent-browser mcp` gives typed tools with read-only annotations, but #161 keeps MCP out of scope for now.
- **Library.** None for Python; only the Rust crate exists.

Compared with Playwright for Python in-process, the CLI route adds a binary, a daemon, a text protocol and a fast-moving 0.x interface, and saves none of the image size. Its gains are in Node-free tooling for shells and coding agents, which kinby is not.

## Recommendation

Keep Playwright for Python behind kinby's own tools, as #920 recommends. Borrow these parts of agent-browser's design:

1. A snapshot with an interactive-only mode as the default view, with depth and selector scope, and a hard character cap.
2. Refs that stay stable across re-renders, fall back to a unique role and name, and fail without acting when ambiguous.
3. Clicks that fail and name the covering element when the target is obscured, instead of reporting success.
4. Action categories mapped onto the gate: snapshot, get, read, wait and scroll are reads; navigate, click, fill, upload, download, eval and state are writes. Leave `eval` out of v1.
5. Human takeover through a CDP screencast relayed by the hub, with mouse and keyboard input sent back, instead of noVNC and Xvfb.
6. Periodic save of storage state while the browser is open, not only on close.

Revisit agent-browser if kinby later wants a Node-free browser that runs outside the instance's Python process, or if building the screencast takeover in Python proves costly.
