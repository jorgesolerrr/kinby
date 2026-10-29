# How AI apps lay out and style their UI

Research for [#976](https://github.com/jorgesolerrr/kinby/issues/976), on the map [#975](https://github.com/jorgesolerrr/kinby/issues/975). Gathered 2026-10-29.

Primary reference: Claude desktop and Claude Code desktop. Secondary: ChatGPT and Codex. Contrast only: Cursor.

## Sources and how to read this

Two kinds of primary source:

- **Official docs.** The Claude Code desktop reference ([code.claude.com/docs/en/desktop](https://code.claude.com/docs/en/desktop), "desktop docs" below), the Claude artifacts help article ([support.claude.com](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)), the ChatGPT/Codex desktop docs at learn.chatgpt.com ([app](https://learn.chatgpt.com/docs/app), [settings](https://learn.chatgpt.com/docs/reference/settings), [browser](https://learn.chatgpt.com/docs/browser)), and Cursor's [agent overview](https://cursor.com/docs/agent/overview).
- **Shipped CSS.** The stylesheets the public login pages load, read on 2026-10-29. Claude: `assets-proxy.anthropic.com/claude-ai/v2/assets/v1/c6a992d55-Cy_MIZDA.css` and `shared-styles-FPyvMs2o.css`, linked from [claude.ai/login](https://claude.ai/login). ChatGPT: `chatgpt.com/cdn/assets/38e720575f9e.css`, linked from [chatgpt.com](https://chatgpt.com/). These are "Claude CSS" and "ChatGPT CSS" below. Hashed filenames change with each deploy, so the values are a snapshot. Token names are the vendors' own; values are copied, not rounded.

The ChatGPT stylesheet is shared with the Codex desktop app: it carries `--codex-window-zoom`, `--codex-content-font-family` and `--vscode-*` fallbacks. So one token set covers both OpenAI products.

Anything marked **unverified** comes from general knowledge of the apps or third-party write-ups, not from a source above. Screenshots were not captured: the apps sit behind sign-in.

## Summary: conventions kinby should follow

| Convention | Claude | Claude Code | ChatGPT | Codex | Cursor |
| --- | --- | --- | --- | --- | --- |
| Left sidebar: New, projects, then a list of recent threads | yes | yes | yes | yes | no (editor first) |
| Thread is a single centred column, ~720-768px | yes | yes | yes | yes | no (side pane) |
| User turn in a tinted bubble on the right; assistant turn unboxed, full column width | yes | yes | yes | yes | – |
| Composer: rounded box pinned to the bottom, a "+" menu at left, model picker and send at right | yes | yes | yes | yes | similar |
| Tool calls collapsed to one-line summaries by default, expandable | yes | yes | yes | yes | yes |
| Side panes (artifact, diff, browser, terminal) open beside the thread, resizable | yes | yes | yes | yes | yes |
| Settings: one surface with a left list of sections | yes | yes | yes | yes | yes |
| Empty state: greeting plus the composer, nothing else | yes | yes | yes | yes | – |
| System sans for UI, near-neutral greys, one brand accent used sparingly | yes | yes | yes | yes | yes |

## App shell

**Sidebar on the left, with three bands.** A top band of actions (New, search), a middle band of containers (projects), and a long tail of recent threads.

- Claude Code: "**+ New** (Cmd+N / Ctrl+N)" starts a session; sessions are "grouped by folder by default", with a **Group and sort** control on the list heading, and archived sessions behind **View all → Archived**. A **Customize** entry manages connectors, skills and plugins. ([desktop docs](https://code.claude.com/docs/en/desktop))
- Claude: "Projects" sit in the sidebar, and artifacts get their own sidebar tab: "Everything you make is saved to the Artifacts tab in your Claude sidebar." ([projects](https://support.claude.com/en/articles/9517075-what-are-projects), [artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them))
- Codex: one project per codebase; threads listed under a **Threads** label with a filter icon that switches to **Chronological**; archived threads live in Settings. ([Codex troubleshooting via search result](https://developers.openai.com/codex/app/troubleshooting))
- ChatGPT: "New chat", with a "Quick chat" icon on its right; "Projects and chats" keep related work together. ([app](https://learn.chatgpt.com/docs/app)) A separate **Library** tab for uploaded files and a shared **Pinned** section are reported by third parties. **Unverified.**
- Recents are a flat, title-only, newest-first list with a hover "…" menu (rename, pin/star, archive, delete). The ChatGPT CSS has `--sidebar-row-action-width` and `--sidebar-row-menu-width` for that hover action. Item text: **unverified.**

**Top-level modes are tabs at the top of the sidebar.** Claude desktop splits into **Chat**, **Cowork** and **Code** ([desktop docs](https://code.claude.com/docs/en/desktop)). ChatGPT desktop has "the toggle above the composer to select Chat or Work", and Codex sits inside it ([app](https://learn.chatgpt.com/docs/app)). Exact placement of the switch: **unverified.**

**Settings and the account sit at the bottom.** The account/avatar row at the sidebar foot opens a menu with Settings, help and sign-out in Claude and ChatGPT. **Unverified** (not in docs), but consistent across both apps.

**Collapse.** The sidebar collapses to a slim icon rail (Claude) or hides fully behind a toggle (ChatGPT), with a header toggle to bring it back. **Unverified**: neither vendor documents it. ChatGPT's sidebar is user-resizable, per a community post ([forum](https://community.openai.com/t/chatgpt-sidebar-improvements-fixed-project-expansion-plus-and-adjustable-sidebar-width-all-users/1182272)); the CSS has `--sidebar-preferred-width`.

**A per-thread title bar.** Claude Code puts the session title at the top with a caret menu (**Transcript view**, **Open in**), pane toggle buttons (**Terminal**, **Changes**, **Browser**, **Files**) and a **⋮** overflow ([desktop docs](https://code.claude.com/docs/en/desktop)). A right-side **session rail** shows cards (Changes `+12 -1`, Context %, Environment) and hides when the chat is too narrow.

## Thread

**Column width.** Both vendors cap the reading column near 48rem.

- Claude CSS: `--chat-column-measure` is `720px` by default, with `960px`, `1280px` and `850px` variants; the gutter is `clamp(16px, …, 32px)` with a `768px` fallback measure.
- ChatGPT CSS: `--thread-content-max-width` takes `40rem`, `42rem` and `48rem`, with `--thread-content-margin` defaulting to `24px`.

**User vs assistant.** The user's turn is a tinted bubble, right-aligned and capped; the assistant's turn is plain prose with no bubble and no avatar, full column width.

- Claude CSS: `--cds-message-bubble-measure: min(85%, 29.5rem)`.
- ChatGPT CSS: `--user-chat-width: 70%` or `min(456px, 100%)`, and `--user-message-background-color` is `alpha-05` in light, `alpha-08` in dark: a faint grey, not the accent.
- Right alignment and the absence of assistant avatars: **unverified** (visible in the apps, not stated in a source).

**Spacing rhythm.** Claude derives gaps from the body line: `--chat-item-gap = body × leading × 0.5`, `--chat-turn-gap = body × leading × 0.75`, with leading near `1.43`, and `--chat-reply-gap: 28px` (Claude CSS).

**Composer.** A rounded box pinned to the thread's bottom, the same width as the column, grows with input.

- Controls: Claude Code's **+** menu holds **Add files or photos**, **Slash commands**, **Add connectors**, **Add plugins**, **Add folder**; the row under the prompt box holds the environment selector, project folder, mode selector, and the **model picker toward the right** with **Effort** inside it, plus a usage ring ([desktop docs](https://code.claude.com/docs/en/desktop)). **Enter** sends; the send button becomes a stop button while running; typing during a run sends a correction.
- ChatGPT/Codex CSS: `--composer-radius` is `calc(var(--spacing) * 7)` = 28px (or `5.5` = 22px), on an elevated surface with `--elevation-composer` shadow; the editor's min height is `calc(var(--spacing) * 10)` = 40px; attachments sit inside it with `--composer-attachment-inset: 8px`; a single-line composer goes fully round (`--radius-full`). Dropdown labels inside it (`--composer-dropdown-label-color`) use secondary/tertiary text.
- Attachments render as chips or thumbnails above the text inside the composer. **Unverified** beyond the inset token.
- Cursor queues messages "below the active task", reorderable, with **Send now** to steer ([Cursor](https://cursor.com/docs/agent/overview)).

**Streaming.** Text appears token by token in place; a "working" row shows while the agent runs (Claude CSS has `--working-row-height` and `--chat-host-working-row-room`). Visual details: **unverified.**

**Tool calls and steps.** Collapsed by default, one summary line each, expandable. Claude Code's **Transcript view** has three modes: **Normal** ("Tool calls collapsed into summaries, with full text responses"), **Thinking** (adds reasoning) and **Verbose** ("Every tool call, file read, and intermediate step"); **Ctrl+O** cycles them ([desktop docs](https://code.claude.com/docs/en/desktop)). Cursor shows a checkpoint per request with **Restore Checkpoint** ([Cursor](https://cursor.com/docs/agent/overview)). Background work (subagents, shells) lives in a separate **Background tasks** pane, not inline.

**Permission prompts** render as an inline card in the thread with explicit buttons: "**Allow for this session** or **Deny**" ([desktop docs](https://code.claude.com/docs/en/desktop)).

**Errors** render as an inline card with a short title and a detail action: `Couldn't connect to <host>` with **View details** ([desktop docs](https://code.claude.com/docs/en/desktop)); artifacts that fail get a **Try fixing with Claude** button ([artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)). Danger text is `#a42528` light / `#f17778` dark (Claude CSS `--cds-text-danger`).

## Side panes

**Panes open to the right of the thread and split the window.** The thread keeps its column; the pane takes the rest.

- Claude artifacts: an artifact "opens in its own window beside your conversation"; legacy artifacts have controls at the top of the panel to view code, copy, or download ([artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)).
- Claude Code: panes for chat, diff (**Changes**), **Browser**, **Terminal**, **Files**, **Plan**, **Tasks**, **Subagent**. "Drag a pane by its header to move it, or drag an edge to resize. **Cmd+\\** closes the focused pane." A pane can pop out into its own window. Browser opens on **Cmd+Shift+B**, terminal on **Ctrl+`**. Two sessions can sit in **Split view** ([desktop docs](https://code.claude.com/docs/en/desktop)).
- ChatGPT/Codex browser: **split view** puts "chat and the browser tab side by side"; **full view** expands the tab; **Cmd+Shift+B** toggles ([browser](https://learn.chatgpt.com/docs/browser)). The diff pane shows staged, unstaged, branch-vs-main, or **Last turn changes**; the terminal toggles on **Cmd+J** (Codex, via [search result](https://developers.openai.com/codex/app/features)).
- Shared shape: a pane has a header with a title, a few icon actions and a **⋮** overflow (Claude's browser **⋮** holds **Keep cookies**, **Clear browsing data**, **Auto-verify changes**).
- Both vendors converge on the **same shortcut for the browser pane: Cmd/Ctrl+Shift+B.**

## Settings

**One settings surface, sections listed down the left.** Claude: **Settings > Code** among other sections (Capabilities, connectors) ([desktop docs](https://code.claude.com/docs/en/desktop)). ChatGPT desktop: a "settings panel" opened from the app menu or **Cmd+,**, with sections General, Profile, Keyboard shortcuts, Notifications, Appearance, Browser, Computer Use, Personalization, Suggested prompts, Memories, Archived chats ([settings](https://learn.chatgpt.com/docs/reference/settings)). On the web, both open settings as a large modal dialog with a left tab list. **Unverified** (observed, not documented). Desktop builds treat it as its own page/window. Either way the structure is a left list of sections and a right form.

## Empty states and first run

The new-thread page is the composer, centred vertically, with a one-line greeting above it and optional suggestion chips. Claude Code's new-session page is "the prompt box plus that control row" for environment, folder, mode and model ([desktop docs](https://code.claude.com/docs/en/desktop)). ChatGPT has a separate `--home-composer-inline-inset` and `--min-rows-composer-home`, so its home composer is taller than the in-thread one (ChatGPT CSS). Claude Code offers **Prompt suggestions**, accepted with **Tab** ([desktop docs](https://code.claude.com/docs/en/desktop)). Greeting copy and chips: **unverified.**

## Foundations

### Type

- **UI sans.** Claude: `--font-ui: var(--font-anthropic-sans)`, falling back to `--cds-font-system: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, …`. ChatGPT: `--font-sans-default: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`, with an `"OpenAI Sans"` brand face. Both are a custom brand sans over a system stack.
- **Response font.** Claude renders replies in a serif: `--font-claude-response: var(--font-anthropic-serif)` (fallbacks Georgia, Times), switchable back to `--font-ui`. ChatGPT renders replies in its sans (`--font-content: var(--font-sans)`). The serif is Claude's signature, not a shared convention.
- **Mono.** Both use `ui-monospace, "SF Mono", Menlo, Consolas, …`.
- **Scale.** Small and dense. Claude's `--cds-rem-scale` is `16/17`, and its body size steps through 12, 13, 14, 15 and 17px (`.75rem` to `1.0625rem`); captions 10-13px; headings 14-15px in UI chrome. ChatGPT: `--text-base` 14px or 16px, `--font-heading-md-size: 1.25rem`, `--font-heading-lg-size: 1.5rem`. So: 14px UI text, 15-16px reading text, headings rarely above 24px.
- Accessibility font options: Claude ships `Atkinson Hyperlegible Next` and `OpenDyslexic` (Claude CSS).

### Colour

- **Neutrals carry the UI.** Claude light uses warm off-whites: `--bg-100: 48 33.3% 97.1%`, `--bg-200: 53 28.6% 94.5%`, `--bg-300: 48 25% 92.2%`, text `--text-100: 60 2.6% 7.6%`. Claude dark uses warm near-blacks: `--bg-100: 60 2.7% 14.5%`, `--bg-300: 60 2.6% 7.6%`, text `48 33.3% 97.1%`. ChatGPT is pure grey: surface `#fff` / `#212121`, secondary `#e8e8e8` / `#303030`, tertiary `#f3f3f3` / `#414141`; text secondary `#5d5d5d` / `#aaa`, tertiary `#8f8f8f`.
- **Text in three or four steps**: primary, secondary (~65% via `color-mix`), tertiary, disabled. Borders are a 10-15% alpha of the foreground (ChatGPT `#0000001a` / `#ffffff26`, or the foreground mixed at 8% in the Codex theme); Claude uses one opaque warm grey for every border step (`--border-100` to `-400`: `51 16.5% 84.5%` light, `30 3.3% 11.8%` dark).
- **Sidebar sits one step darker than the thread** in light mode (Claude `bg-200` vs `bg-100`; ChatGPT `surface-secondary` vs `surface`). Which token each region uses: **inferred** from token names.
- **Accent is rare.** Claude's brand clay (`--accent-brand: 15 63.1% 59.6%`) appears on the logo and a few brand moments; links and focus use a separate blue `--accent-*` (`210 70.9% 51.6%`). ChatGPT's primary button is black-on-white (white-on-black in dark), not coloured. For kinby: kin green as the brand mark and the one accent; the primary button may follow ChatGPT and stay neutral. That last part is a recommendation, not a finding.
- **Git colours** are their own tokens: added `#1e9e3c` / `#32d74b`, closed `#ff3a30` (Claude CSS).

### Radius

- Claude: `--cds-radius` steps through 5, 6, 7, 8, 10, 20, 24px (`.3125rem` to `1.5rem`) and a `999px` pill; message blocks use `--msg-block-radius: .5rem`.
- ChatGPT: `--radius-sm` 6px, `--radius-md` 8px, `--radius-lg` 10px, `--radius-xl` 12px, `--radius-2xl` 16px, `--radius-3xl` 20-24px, `--radius-4xl` 24-32px; the composer is 22-28px or full.
- Shared: 6-10px for controls and cards, 16-24px for the user bubble and the composer, full pill for chips and icon buttons.

### Density and spacing

- 4px base unit in both (`--spacing: .25rem` / `4px`, ChatGPT CSS; Tailwind `--spacing` in Claude CSS).
- Claude scales its whole rem by `16/17` and exposes text-size variants (`--cds-font-size-body--sm` … `--lg`), so density is a user setting.

### Buttons

- A neutral primary (filled, high contrast), a secondary with a border or soft fill, a ghost for toolbars, and icon-only buttons. ChatGPT's `--color-background-primary-soft` is `gray-100` light / `gray-300` dark for soft controls. Claude has `--cds-text-button-disabled` `#0007` / `#fff6`. Full variant list and heights: **unverified.** Sizes in practice: 32px for toolbar and composer buttons, 36px for form buttons. **Unverified.**

### Icons

- Claude ships its own icon font, `Anthropicons-Variable` / `Anthropicons-FizzyLake` (Claude CSS). ChatGPT's CSS names no public icon library. Both draw thin (about 1.5px) line icons at 16-20px. Stroke and size: **unverified.** For kinby the closest public set is Lucide, which shadcn already uses.

## Mobile

- The sidebar becomes an off-canvas drawer opened from a header button; the thread goes full width; the composer stays pinned to the bottom. **Unverified** (observed behaviour, not documented).
- Side panes become separate screens or tabs: Claude's mobile apps show artifacts in an **Artifacts** tab, with no side-by-side pane ([artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)).
- The panel layout degrades by width: Claude's session rail "appears only while the chat is wide enough" ([desktop docs](https://code.claude.com/docs/en/desktop)); Claude's chat gutter shrinks from 32px to 16px via `clamp()` and ChatGPT switches layout on a `48rem` container query (both CSS files).

## Cursor, as contrast

Cursor is editor first. The agent lives in a sidepane opened with **Cmd+I** next to the code, not in a centred thread; history and steering live in a separate **Agents window** with **Cmd+K** search; reviews go through checkpoints in the chat timeline ([Cursor](https://cursor.com/docs/agent/overview)). kinby is a chat-first teammate, so the Claude/ChatGPT shell fits it; Cursor's sidepane fits only as a pattern for kinby's own side panes.
