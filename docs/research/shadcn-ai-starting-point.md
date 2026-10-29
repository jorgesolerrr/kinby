# Which shadcn preset and AI blocks to start from

Research for [#977](https://github.com/jorgesolerrr/kinby/issues/977), on the map [#975](https://github.com/jorgesolerrr/kinby/issues/975). Sources checked on 2026-10-29. Claims marked **judgment** are my reading, not something a source states.

## Answer

- **Stay on Base UI.** It has been shadcn's default since July 2026, and every block, the chat components, and the new AI patterns ship a Base UI variant ([Base UI as the Default](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default), [Blocks for Radix and Base UI](https://ui.shadcn.com/docs/changelog/2026-02-blocks), [Trace, Code Block and AI Elements](https://ui.shadcn.com/docs/changelog/2026-10-ai-elements)).
- **Prototype on `luma`, with `rhea` as the denser fallback, and keep `nova` as the control.** Luma is rounded with soft elevation and open spacing ([Luma](https://ui.shadcn.com/docs/changelog/2026-03-luma)). Rhea is "a more compact Luma" ([Rhea](https://ui.shadcn.com/docs/changelog/2026-05-rhea)). **Judgment:** Claude desktop and ChatGPT read as rounded, soft and roomy around the conversation, which is Luma's brief; settings, stats and the factory pages may want Rhea's density. Nova is today's compact look, so it is the baseline to compare against.
- **Build chat from shadcn's own chat components and AI Elements, not from Vercel's AI SDK Elements.** Vercel's repo says AI Elements is moving to shadcn/ui and will be deprecated at the end of November 2026 ([vercel/ai-elements](https://github.com/vercel/ai-elements)). The shadcn version is copy-in patterns with Base UI variants ([AI Elements](https://ui.shadcn.com/docs/ai-elements)).
- **Build ourselves:** the artifact / side pane, the web preview, and any canvas. shadcn's AI Elements does not rebuild these; it points to Card, Button, Tooltip, an `iframe` with Input Group, and React Flow ([migration table](https://ui.shadcn.com/docs/ai-elements#migrating-from-ai-sdk-elements)).

## What apps/web uses today

- `apps/web/components.json`: `style: base-nova`, `baseColor: neutral`, `iconLibrary: lucide`, `menuColor: default`, `menuAccent: subtle`, no extra registries.
- `bunx --bun shadcn@latest preset resolve` in `apps/web` gives preset `b2fA`: style `nova`, font `geist`, radius `default`, theme and chart color `neutral` ([preset builder link](https://ui.shadcn.com/create?preset=b2fA)).
- `apps/web/src/index.css` holds the brand on top of that preset:
  - Fonts: Geist (sans), Geist Mono, and Gabarito for `--font-heading`, applied to `h1`–`h4`.
  - Tokens: `--kin` (brand green) and `--ember` (marigold) are custom. `--primary`, `--ring`, `--sidebar-primary` and `--chart-1` point at `--kin`. Surfaces are green-tinted in light mode and near-black neutrals in dark mode. `--radius` is `0.625rem`.
  - Custom tokens outside shadcn's set: `--success`, `--on-ember`, six `--avatar-*` colors.
  - It imports `shadcn/tailwind.css`, which carries the `scroll-fade` and `shimmer` utilities ([chat components](https://ui.shadcn.com/docs/changelog/2026-06-chat-components)), and sources Streamdown's classes.
- `apps/web/src/components/ui/` holds 33 registry components, including the chat primitives `bubble`, `marker` and `message-scroller` (backed by `@shadcn/react`). Not yet installed: `message`, `attachment`, `trace`, `code-block`, `collapsible`, `resizable`.
- `apps/web/ds-bundle/` does not exist in the repo at this commit, so there is nothing there to account for.

## What a re-theme touches

A shadcn style is not only CSS variables: Luma, Sera and Rhea each change component geometry, spacing and sizes in the component source ([Sera](https://ui.shadcn.com/docs/changelog/2026-04-sera), [Rhea](https://ui.shadcn.com/docs/changelog/2026-05-rhea)). So switching style has two layers:

1. **Theme and font only** — `shadcn apply <code> --only theme,font` rewrites CSS variables and fonts without reinstalling components (shadcn skill CLI reference; [Partial Preset Apply](https://ui.shadcn.com/docs/changelog/2026-04-partial-preset-apply)). Our brand tokens live in the same `:root`/`.dark` blocks, so this needs a merge by hand to keep `--kin`, `--ember`, `--success` and the avatar colors.
2. **Style** — `shadcn apply <code>` overwrites the installed components with the new style's source ([shadcn apply](https://ui.shadcn.com/docs/changelog/2026-04-shadcn-apply)). Every file in `src/components/ui/` changes, and any local edits there are lost unless merged with `add --diff`. `components.json`'s `style` changes from `base-nova` to `base-luma` (or `base-rhea`).

For `/proto/*` the cheap path is a scratch copy: init a throwaway Vite app with `--base base` and each candidate preset, or add the candidate's components under a proto-only folder, rather than flipping the app's `components.json`. **Judgment.**

## Styles available for Base UI

The 2025 launch rebuilt every component for Base UI and introduced five styles ([shadcn create](https://ui.shadcn.com/docs/changelog/2025-12-shadcn-create)); three more followed. Trace and Code Block ship "across all eight styles" ([changelog](https://ui.shadcn.com/docs/changelog/2026-10-ai-elements)).

| Style | Source's description | Density | Radius | Type |
|---|---|---|---|---|
| vega | "The classic shadcn/ui look" | medium | default | not stated |
| nova (ours) | "Reduced padding and margins for compact layouts" | compact | not stated | not stated |
| maia | "Soft and rounded, with generous spacing" | roomy | rounded | not stated |
| lyra | "Boxy and sharp. Pairs well with mono fonts" | not stated | sharp | suits mono |
| mira | "Compact. Made for dense interfaces" | dense | not stated | not stated |
| luma | "Rounded geometry. Soft elevation. Breathable layouts." macOS Tahoe without the glass | open | rounded | not stated |
| sera | "Minimal. Editorial. Typographic." | not stated | square | serif headings, uppercase |
| rhea | "A more compact Luma" | dense | rounded | not stated |

Sources: [shadcn create](https://ui.shadcn.com/docs/changelog/2025-12-shadcn-create), [Luma](https://ui.shadcn.com/docs/changelog/2026-03-luma), [Sera](https://ui.shadcn.com/docs/changelog/2026-04-sera), [Rhea](https://ui.shadcn.com/docs/changelog/2026-05-rhea).

Lyra and Sera fight the look we want (sharp, editorial); maia overlaps luma. **Judgment.**

## Blocks and components for an AI app

All of these come from the `@shadcn` registry and resolve for `base-nova` (checked with `bunx --bun shadcn@latest view trace code-block` in `apps/web`).

| Need | Start from | Notes |
|---|---|---|
| App sidebar | `sidebar-07` (collapses to icons) | Closest to Claude's and ChatGPT's left rail. `sidebar-08` is the inset variant ([sidebar blocks](https://ui.shadcn.com/blocks/sidebar)). Blocks exist for Base UI ([changelog](https://ui.shadcn.com/docs/changelog/2026-02-blocks)). |
| Conversation scroll | `message-scroller` (installed) | Anchoring, streaming follow, jump-to-latest ([Message Scroller](https://ui.shadcn.com/docs/components/base/message-scroller)). |
| Message row and surface | `message`, `bubble` (installed), `attachment`, `marker` (installed) | [chat components](https://ui.shadcn.com/docs/changelog/2026-06-chat-components) |
| Composer | AI Elements **Prompt Input** | Composes `input-group`, `attachment`, `dropdown-menu` and Model Selector; Enter sends, Shift+Enter adds a line ([Prompt Input](https://ui.shadcn.com/docs/ai-elements/base/prompt-input)). |
| Tool call, reasoning, plan, subagent | `trace`, then AI Elements **Tool**, **Reasoning**, **Plan**, **Subagent**, **Confirmation** | Trace lines take `running`, `error`, `pending` status and nest ([Trace](https://ui.shadcn.com/docs/components/base/trace)). Confirmation is approve/deny before a tool runs ([AI Elements](https://ui.shadcn.com/docs/ai-elements)). |
| Code and terminal output | `code-block`, AI Elements **Terminal** | Code Block depends on `use-highlighted-code`, which pulls `shiki` (registry check). We already render replies with Streamdown and `@streamdown/code`, so prototypes should pick one highlighter. |
| Sources, citations, suggestions | AI Elements **Sources**, **Inline Citation**, **Suggestion** | [AI Elements](https://ui.shadcn.com/docs/ai-elements) |
| Questions to the user | `questionnaire` | [Questionnaire](https://ui.shadcn.com/docs/components/base/questionnaire) |

The AI Elements examples are wired to the Vercel AI SDK's message parts (`useChat`, `toUIMessageStreamResponse`), but the patterns are copied in and the components are not described as requiring the SDK ([AI Elements](https://ui.shadcn.com/docs/ai-elements), [Prompt Input](https://ui.shadcn.com/docs/ai-elements/base/prompt-input)). kinby's thread events would need an adapter to those part shapes, or we pass props directly. **Unverified** how much each pattern assumes AI SDK types; check per pattern when installing.

## Gaps we would build ourselves

- **Artifact / side pane.** Not rebuilt; shadcn suggests Card, Button and Tooltip ([migration table](https://ui.shadcn.com/docs/ai-elements#migrating-from-ai-sdk-elements)). **Judgment:** `resizable` beside the thread, or `sidebar-15`'s right sidebar, gives the Claude-style split.
- **Web preview** (our live browser). Not rebuilt; shadcn suggests an `iframe` with Input Group (same source). kinby's `live-browser.tsx` already owns this.
- **Canvas.** Not rebuilt; shadcn points to React Flow (same source). Not needed for the first prototypes.
- **Home / empty composer screen** (the centered "what can I do" page). No block covers it. **Unverified:** I found no shadcn block for it; compose it from `empty`, Prompt Input and Suggestion.
- **Brand.** The kinby mark, wordmark and the kin-green accent stay ours; no preset carries them.
