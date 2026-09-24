# shadcn/ui for the kinby web app: Tailwind, agent skills, and lint

Wayfinder ticket: jorgesolerrr/kinby #275, part of the map #211. Date: 2026-09-24.
Builds on `docs/research/web-app-stack.md` (branch `research/web-app-stack`) and the choices settled in #218: Bun workspaces at the root (`apps/web`, `packages/contract`), Bun as package manager and script runner only, Vite 8, React 19, TypeScript 7, Vitest, oxlint (type-aware) and oxfmt, and a root `bun run check`.

Questions:

1. Does shadcn/ui work without Tailwind? If not, what is the smallest setup so nobody writes Tailwind config by hand?
2. Which agent skills do shadcn and nearby projects publish, and how do they go into `.claude/skills` and `AGENTS.md`? The project rejects MCP (map #211, Out of scope).
3. Which linters catch slop in agent-written React and TypeScript, and which belong in `bun run check`?

Sources: the shadcn-ui/ui repo on `main` (CLI source, templates, `skills/`, changelog MDX files), ui.shadcn.com docs, the shadcn-ui/lint repo, the Tailwind and oxc docs, the millionco/react-doctor repo, the vercel-labs and anthropics skill repos, and the knip docs. Versions come from the npm registry on the date above. Beyond reading, I scaffolded a throwaway app with create-vite, ran `shadcn init`, added the sidebar block and the chat components, built it, and ran oxlint (with `@shadcn/lint`), react-doctor, and knip on it. The results below come from those runs. Bun was not installed on the machine, so the commands ran under npm/npx with Node 24.13; the flow is the same with `bunx --bun`.

## Short answers

1. **Tailwind is required.** `shadcn init` exits with "No Tailwind CSS configuration found" if Tailwind is missing, and every component is written in Tailwind classes. Nobody has to write Tailwind config, though. Tailwind v4 has no `tailwind.config.js`: you install two packages, add one Vite plugin line and one CSS line, and `shadcn init` generates the rest (theme tokens, `components.json`, `cn`). The one Tailwind the coder writes is layout classes (`flex`, `gap-4`, `w-full`) in JSX, and the linter below limits even those.
2. **The official skill is `shadcn`**, in `shadcn-ui/ui/skills/shadcn`. It needs no MCP; it drives the `shadcn` CLI (`search`, `docs`, `add --dry-run/--diff`, `info`). Install it with `bunx skills add shadcn/ui --skill shadcn -a claude-code --copy`. One change is needed for this repo: its context line runs `npx shadcn@latest info --json` from the repo root, and there it reports "Manual" with no Tailwind, so it must point at `apps/web`. Two Vercel skills are worth adding next to it (`vercel-react-best-practices`, `vercel-composition-patterns`).
3. **The linter the maintainer means is `@shadcn/lint`** (repo `shadcn-ui/lint`, first published 2026-09-14, now 0.2.0, MIT). It is "an agent-first linter for Tailwind design systems" and runs as an oxlint JS plugin. Its six rules stop the typical agent UI slop: restyling a component through `className`, raw palette colours, arbitrary values, inline styles, classes Tailwind cannot generate, and dynamic class strings. Each error names the fix from your own components, for example "Use a size (sm, lg)". Put it inside the same oxlint run, next to oxlint's `react`, `jsx-a11y`, and type-aware `typescript` rules. Add knip for dead code. Leave react-doctor out of the gate.

## 1. Is Tailwind a hard dependency?

Yes, for shadcn/ui as a product: the CLI, the registry, the skill, and the linter all assume Tailwind v4.

- The Vite install page says "Install Tailwind CSS (required)" (https://ui.shadcn.com/docs/installation/vite).
- `packages/shadcn/src/preflights/preflight-init.ts` marks `TAILWIND_NOT_CONFIGURED` when no Tailwind version is detected, and init stops. Run on a fresh create-vite app, `shadcn init -b base -p nova` printed `✖ Validating Tailwind CSS.` and "Install Tailwind CSS then try again." It also failed `Validating import alias.` because the template has no `@/*` path.
- Components ship as Tailwind classes (`buttonVariants` is a `cva` over utility classes), and the global CSS imports `tailwindcss`, `tw-animate-css`, and `shadcn/tailwind.css`, which holds shared custom variants like `data-open:` (changelog 2026-05, "shadcn eject").

What exists without Tailwind, and why it does not fit:

- `@shadcn/react` 0.3.1, "Unstyled components for React", holds headless pieces such as the `MessageScroller` logic (changelog 2026-06, chat components). It is not the component set.
- Base UI (`@base-ui/react` 1.8.0) or React Aria on their own, styled with CSS modules. That is a hand-built design system: no registry, no `shadcn add`, no skill, no linter. It is the opposite of "go straight to shadcn".

So "no Tailwind" is not possible with shadcn. What the maintainer wants, no Tailwind config written by hand, is how Tailwind v4 works by default.

### What Tailwind v4 costs

Tailwind v4 is configured in CSS, with no JS config. The Vite guide is: install `tailwindcss @tailwindcss/vite`, add `tailwindcss()` to `plugins`, and put `@import "tailwindcss";` in the CSS (https://tailwindcss.com/docs/installation/using-vite). shadcn's `components.json` accordingly has `"tailwind": { "config": "" }` for v4 (template `templates/vite-monorepo/apps/web/components.json`). Current versions: `tailwindcss` and `@tailwindcss/vite` 4.3.3, `shadcn` 4.21.0.

### Minimal setup in `apps/web` (verified)

Start from create-vite's `react-ts` template as #218 decided. The shadcn `vite` template (`templates/vite-app`) is not a good base: it ships ESLint, Prettier, and `prettier-plugin-tailwindcss`, and TypeScript `~6`.

1. `bun add tailwindcss @tailwindcss/vite`
2. `vite.config.ts`:
   ```ts
   import path from "node:path"
   import tailwindcss from "@tailwindcss/vite"
   import react from "@vitejs/plugin-react"
   import { defineConfig } from "vite"

   export default defineConfig({
     plugins: [react(), tailwindcss()],
     resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
   })
   ```
3. `src/index.css`: `@import "tailwindcss";`
4. Add `"paths": { "@/*": ["./src/*"] }` to `compilerOptions` in `tsconfig.json` and `tsconfig.app.json`. Leave out `baseUrl`, which TypeScript 6 deprecated. (Since changelog 2026-05, `package.json` `imports` also work as an alias.)
5. `bunx --bun shadcn@latest init -b base -p nova` (use `--base` for the library and `--preset` for the style).

Step 5 wrote `components.json` (`"style": "base-nova"`, `"tailwind": {"config": "", "css": "src/index.css", "baseColor": "neutral", "cssVariables": true}`, `"iconLibrary": "lucide"`, aliases under `@/`), replaced `src/index.css` with the theme (an `@theme inline` block that maps `--color-*` and `--radius-*` to OKLCH variables in `:root` and `.dark`, sidebar tokens included), and generated `src/lib/utils.ts` as the single line `export { cn } from "cn"`. It added these dependencies: `@base-ui/react`, `cn`, `class-variance-authority`, `lucide-react`, `tw-animate-css`, `@fontsource-variable/geist`, and `shadcn` (for `shadcn/tailwind.css`; `shadcn eject` inlines that file and removes the dependency). `vite build` passed after adding the sidebar block and the chat components.

Bun: the skill and the docs both use the runner form `bunx --bun shadcn@latest`. The CLI detects the package manager from the lockfile and has no `--package-manager` flag (`skills/shadcn/cli.md`).

### Base UI or Radix

Use Base UI. It has been the default since 2026-07-02: "New projects default to Base UI … Radix is not being deprecated" (changelog `2026-07-base-ui-default`). Base UI is at 1.8.0. Every component ships for both bases, React Aria has been a third base since 2026-07-17, and new projects on shadcn/create pick Base UI over Radix two to one. The visible API difference is `render` (Base) instead of `asChild` (Radix) on triggers; `rules/base-vs-radix.md` in the skill lists the rest. For a new app there is nothing to migrate, so take the default. Pass `-b base` explicitly anyway, because a non-interactive init that does not name a base follows whatever the default is at the time.

### Sidebar and chat for a Grok-style shell

`bunx --bun shadcn@latest search @shadcn -q sidebar` lists `sidebar` (ui) and blocks `sidebar-01` to `sidebar-16`. For a chat shell, the relevant ones are `sidebar-07` ("A sidebar that collapses to icons"), `sidebar-08` ("An inset sidebar with secondary navigation"), and `sidebar-16` ("A sidebar with a sticky site header"). The component is `SidebarProvider` > `Sidebar` (`SidebarHeader`/`SidebarContent`/`SidebarFooter`) + `SidebarInset`, with `collapsible="offcanvas" | "icon" | "none"` and `variant="sidebar" | "floating" | "inset"` (https://ui.shadcn.com/docs/components/sidebar). `add sidebar-07` wrote `app-sidebar.tsx`, `nav-main.tsx`, `nav-projects.tsx`, `nav-user.tsx`, `team-switcher.tsx`, and nine ui files. On Vite there is no page file, so the block does not add a route.

The more important find for kinby: since 2026-06-26 shadcn has chat components, `MessageScroller`, `Message`, `Bubble`, `Attachment`, and `Marker` (`add message-scroller message bubble attachment marker`). `MessageScroller` handles "anchored turns, streamed replies, saved thread restore, prepended history, jump-to-message" and owns no transport or state (changelog `2026-06-chat-components`). This covers the scroll logic the web-app-stack research planned to hand-write or skip. The 2026-08 `questionnaire` component and `@shadcn/helpers` (AI SDK approval flows) exist too, but they target the AI SDK and kinby has its own contract.

## 2. Agent skills

### The official `shadcn` skill

`shadcn-ui/ui` has two skills in `skills/`: `shadcn` and `migrate-radix-to-base`. The docs page is https://ui.shadcn.com/docs/skills. It came out with CLI v4 on 2026-03-06: "shadcn/skills gives coding agents the context they need to work with your components and registry correctly" (changelog `2026-03-cli-v4`).

What `skills/shadcn/SKILL.md` does:

- Injects project context at load time: ``!`npx shadcn@latest info --json` `` (framework, Tailwind version, `base`, aliases, `iconLibrary`, installed components, resolved paths).
- Principles: search the registries before writing custom UI, compose instead of reinventing, use built-in variants before custom styles, use semantic colours (`bg-primary`, never `bg-blue-500`).
- "Critical Rules" with Incorrect/Correct pairs in `rules/*.md`: `className` for layout only; `gap-*` instead of `space-y-*`; `size-*` when width equals height; no manual `dark:` colours; `cn()` for conditionals; forms with `FieldGroup` + `Field`; items inside their Group; `render` vs `asChild`; dialogs always have a Title; `Alert`, `Empty`, `Skeleton`, `Badge`, `Separator` instead of styled divs; icons with `data-icon` and no size classes; chat with `MessageScroller`/`Message`/`Bubble`, "Don't write a `useStickToBottom`/`ResizeObserver` hook".
- Workflow: check installed components, `search`, `docs <component>` and fetch the URLs, `add --dry-run`/`--diff` before overwriting, read every added file and fix it, never guess a registry, "NEVER fetch raw files from GitHub manually".
- `allowed-tools: Bash(npx shadcn@latest *), Bash(pnpm dlx shadcn@latest *), Bash(bunx --bun shadcn@latest *)`.

MCP: the skill works through the CLI alone. `mcp.md` documents `shadcn mcp` as an option, and the docs page says the skill "optionally connects" to it. The MCP reference itself says that project configuration comes from `npx shadcn@latest info` and "there is no MCP equivalent". Nothing is lost without MCP. Skip `shadcn mcp init`, which would write `.mcp.json`.

Other agent-facing pieces:
- `https://ui.shadcn.com/llms.txt`: a doc index for agents. The skill's `docs` command already returns per-component URLs, so there is no need to reference it separately.
- Presets: a short code for the whole design system (colours, fonts, radius, icons), applied with `init --preset <code>` or `apply <code>`. Record kinby's code in `AGENTS.md` if a custom preset is chosen on shadcn/create.
- `migrate-radix-to-base`: not needed on a new Base UI app.

### Installing into this repo

The repo commits skills as plain directories under `.claude/skills/<name>/` (with `agents/openai.yaml` for Codex). The `skills` CLI (vercel-labs/skills) installs into `.claude/skills/` for `-a claude-code`. Its default is a symlink to a canonical copy, and `--copy` makes real files. Use `--copy`: symlinks are unreliable on Windows, and the coder's workspace is a git clone.

```
bunx skills add shadcn/ui --skill shadcn -a claude-code --copy -y
```

Then make one change to `.claude/skills/shadcn/SKILL.md`. The context line runs from the repo root, and there the CLI reports `"framework": "Manual"`, `"tailwindVersion": null`, `"config": null` (checked from a parent directory; with `-c web` it reported Vite, v4, and alias `@`). Change it to:

```
!`bunx --bun shadcn@latest info --json -c apps/web`
```

Everything else in the skill works as written. The skill ships `agents/openai.yml` and `evals/`; keep both.

`AGENTS.md` gets a short web section, for example: "UI lives in `apps/web` and is built from shadcn/ui (Base UI, preset `nova`). Use the `shadcn` skill; add components with `bunx --bun shadcn@latest add` from `apps/web`, never hand-write a component that exists in the registry. Run `bun run check` and fix every `shadcn/*` error by using a variant or theme token, not by disabling the rule."

### Other skills worth adding

- `vercel-labs/agent-skills` (31k stars, active): `react-best-practices` ("70 rules across 8 categories", React and Next.js performance, MIT) and `composition-patterns` ("boolean prop proliferation … compound components … React 19 API changes", MIT). Both apply to a Vite SPA, although part of `react-best-practices` is about Next.js server code the coder can ignore. `web-design-guidelines` is a review skill that fetches its rules from a URL on every run, which makes it an audit tool rather than a coding guide. Optional.
- `anthropics/skills/frontend-design`: guidance for "distinctive, intentional visual design". It pushes toward a distinct look ("take aesthetic risk"), which conflicts with holding to a shadcn preset. Leave it out, or run it once when choosing the preset.
- `react-doctor install` adds a skill too; see below for why react-doctor stays out.

## 3. Linters against UI slop

### `@shadcn/lint`: the shadcn linter

- **What it is.** `shadcn-ui/lint`, repo created 2026-09-02, npm `@shadcn/lint` first published 2026-09-14, latest 0.2.0 (2026-09-22), seven releases, MIT, about 2,750 stars. Its tagline: "Write design system rules that agents can verify." It "works with Tailwind v4 projects (shadcn/ui not required)", runs on ESLint 9.30+ or oxlint 1.80+, and supports React, Vue, and Svelte. It is not in the ui.shadcn.com changelog yet; the repo README and `docs/` are the source.
- **Rules** (README, `docs/rules/*.md`):

  | Rule | Catches |
  |---|---|
  | `no-restyle` | restyling a component through `className` (padding, colour, shape, typography), with per-component `contracts` and `allow: ["layout"]` |
  | `no-raw-colors` | palette colours like `bg-pink-500`; suggests the nearest theme token |
  | `no-arbitrary-values` | `p-[13px]`; suggests the scale value |
  | `no-inline-styles` | `style={{…}}` and `<style>` |
  | `no-unknown-classes` | classes your installed Tailwind and theme cannot generate (`flex-cols` → "Did you mean flex-col?") |
  | `require-static-classes` | component classes it cannot read, like `` `bg-${color}` `` |

  It reads `components.json` to find components, their `cva` variants and sizes, and the theme file, and it understands `cn`, `cva`, `tv`, and Base UI's `render` prop. Messages take placeholders (`{{sizes}}`, `{{file}}`) and a shared `note`.
- **Why it is built for agents.** The README reports runs across Sonnet 5, Haiku 4.5, Opus 5, and two GPT 5.6 models: 42 to 117 violations before lint feedback and 0 after, "almost every task converges in one round", and 10% to 48% cheaper than giving the model the rules alone (`docs/evals.md`, which names the run ids). These are the vendor's own evals.
- **What it did on the scratch app.** With the six rules on, one file of typical agent output gave:
  - `"p-4" is not allowed on <Button>: <Button> owns its spacing. Use a size (default, xs, sm, lg, icon, …)`
  - `"rounded-full" is not allowed on <Button>: <Button> owns its shape. Use a variant: default, outline, secondary, ghost, destructive, link.`
  - `"bg-blue-500" uses the raw Tailwind palette … Use one of: accent, background, border, …`
  - `"p-[13px]" hardcodes an off-token value. Use "p-3.25" instead`
  - `"flex-cols" is not a class this project's Tailwind knows … Did you mean "flex-col"?`
  - `Inline style sets marginTop. Style through classes`

  It did not flag `` className={`text-${color}-500`} `` on a plain `<span>`, since `require-static-classes` covers component classes only, and it does not flag `space-y-4`, which only the skill forbids.
- **Noise to plan for.** The official `sidebar-07` block files break `no-restyle` 13 times (`"p-0" is not allowed on <DropdownMenuLabel>`, `"aria-expanded:bg-muted"` on `SidebarMenuButton`). Turn `no-restyle` and `no-arbitrary-values` off for `src/components/ui/**`, which is the override the README shows for monorepos. Block files that land outside `ui/` then fail lint the first time, and the coder fixes them as the skill's "review added components" step already asks. `no-unknown-classes` also flagged every leftover class from create-vite's `App.css`, so delete that file when scaffolding.
- **Bun and oxlint.** It runs through `"jsPlugins": ["@shadcn/lint"]` in `.oxlintrc.json`. Its peers (`eslint`, `@typescript-eslint/parser`) are marked optional in `peerDependenciesMeta`, so Bun does not pull in ESLint. oxlint's JS plugin API is alpha (https://oxc.rs/docs/guide/usage/linter/js-plugins, and `docs/react.md` in the lint repo), and JS plugins cannot use type information. The oxlint bin runs on Node (`#!/usr/bin/env node`), and `bun run` honours that shebang unless given `--bun`. Keep Node 24 in the image as web-app-stack.md already plans. I did not test it under the Bun runtime.
- **Maturity.** Pre-1.0 and ten days old on npm, on an alpha plugin API. It is still the right choice: shadcn built it for exactly this problem, it runs in the same process as the rest of the lint, and each rule can be downgraded to `warn` in one line.

### oxlint's own React, a11y, and type-aware rules

oxlint 1.85.0 has 870 rules. The defaults are the `eslint`, `typescript`, `unicorn`, and `oxc` plugins with the `correctness` category. Setting `plugins` replaces the default set, so list them all (https://oxc.rs/docs/guide/usage/linter/plugins.html). Relevant plugins: `react` (79 rules), `react-perf` (4), `jsx-a11y` (32). On the scratch app with `correctness` as error, oxlint reported:

- `react(set-state-in-effect)` and `react(no-deriving-state-in-effects)` for `useEffect(() => setDoubled(count * 2), [count])`, the most common agent pattern. It also fired on shadcn's own `hooks/use-mobile.ts`, so override it for `src/hooks/use-mobile.ts` or accept a one-line fix there.
- `react(no-array-index-key)`, `react/rules-of-hooks`, `react/exhaustive-deps`.
- `jsx-a11y(alt-text)`, `click-events-have-key-events`, `no-static-element-interactions` for a `<div onClick>`.
- `typescript(no-floating-promises)` for `fetch(...).then(...)` in an effect. This is type-aware: `"options": {"typeAware": true}` with `oxlint-tsgolint` 7.0.2003 on TypeScript 7.0.2 worked (https://oxc.rs/docs/guide/usage/linter/type-aware.html).

Noise: turning on the `suspicious` category also enables `react/react-in-jsx-scope`, which is wrong for the React 17+ JSX transform. `react-perf`'s `jsx-no-new-function-as-prop` fires on every inline `onClick`. Keep `correctness` as error, switch `react-in-jsx-scope` off, and leave `react-perf` out. `react/only-export-components` warns on `buttonVariants` and other non-component exports in `ui/` files, so turn it off there.

### Tailwind class linters and formatting

- `@shadcn/lint`'s `no-unknown-classes` does what `eslint-plugin-tailwindcss` 4.4.0 and `eslint-plugin-better-tailwindcss` 4.7.0 do for invalid classes, inside oxlint. Neither ESLint plugin is needed.
- Class order: oxfmt has `sortTailwindcss` built in, "Based on prettier-plugin-tailwindcss. Disabled by default", configured with `stylesheet` and `functions` (https://oxc.rs/docs/guide/usage/formatter/sorting.html). Turn it on in `.oxfmtrc.json` with `{"sortTailwindcss": {"stylesheet": "./src/index.css", "functions": ["cn", "cva"]}}` so class-order diffs never reach review. oxfmt is 0.70.0.

### knip

knip 6.38.0 finds unused files, exports, and dependencies. It has plugins for Bun, oxlint, oxfmt, Vite, Vitest, Tailwind, and TypeScript (187 plugins; https://knip.dev/reference/plugins), and it reads package.json workspaces. On the scratch app it reported the sidebar and chat files as unused because `App.tsx` did not import them, which is the expected result, plus `@base-ui/react`, `cn`, `class-variance-authority`, `lucide-react`, and `@shadcn/react` as unused dependencies. shadcn ui files export every sub-part (`SidebarMenuSub`, `SidebarRail`, …), and most of those are never used, so configure `"ignoreIssues": {"apps/web/src/components/ui/**": ["exports", "types"]}` (https://knip.dev/reference/configuration). Known issue: on Windows with Node 22+, oxc-parser raw transfer can fail under memory pressure; `KNIP_DISABLE_RAW_TRANSFER=1` works around it (https://knip.dev/reference/known-issues). It catches the dead code agents leave behind after a refactor, and it also covers `packages/contract`. It belongs in the gate.

### react-doctor

It exists: `millionco/react-doctor`, "Your agent writes bad React. This catches it", `react-doctor` 0.9.14, about 14.9k stars, first commit 2026-02-13. It wraps oxlint with its own `oxlint-plugin-react-doctor` (287 rules) plus whole-project scans (`deslop/*`, circular and unused dependencies, a Socket.dev supply-chain scan). On the scratch app it found things oxlint's built-in rules did not: `no-fetch-in-effect`, `no-fetch-response-used-without-status-check`, `no-promise-then-side-effect-in-effect-without-catch`, and `no-derived-useState`. It also picked up the `@shadcn/lint` rules from `.oxlintrc.json`.

Reasons to keep it out of the gate:
- License: "Modified MIT". It needs prior written permission for use "as input to any automated pipeline for training or improving any machine learning model or AI system" and for paid hosted offerings (`LICENSE`). kinby is an open-source AI teammate, so this is a real constraint.
- Telemetry to Sentry is on by default (opt out with `--no-telemetry`), and the supply-chain scan calls Socket.dev by default.
- It pins its own `oxlint >=1.81.0 <1.82.0` and `typescript >=5.0.4 <6`, a second toolchain beside the one #218 chose.
- Its output is a scored audit ending in "Ask the user if they would like to set it up". It is not built as a pass/fail check.

Its best per-file rules are published as `oxlint-plugin-react-doctor` and can go in `jsPlugins` one at a time. That package falls under the same licence, so wait until a real gap shows up. `no-fetch-in-effect` matters less in kinby, where data comes over the socket and the store, not `fetch` in components.

### Not recommended

ESLint with `@eslint-react/eslint-plugin` 5.20.8 or `eslint-plugin-react-hooks` 7.1.1 duplicates oxlint. typescript-eslint cannot lint TypeScript 7 (web-app-stack.md). Biome 2.5.14 and `ultracite` 7.12.0 are alternative toolchains, not additions.

## Recommendation

**UI:** Tailwind v4 through `@tailwindcss/vite`, set up by the five steps above. shadcn with `-b base -p nova`, or a custom preset code from shadcn/create recorded in `AGENTS.md`. The shell from `sidebar-07` or `sidebar-08`, and the thread from `MessageScroller`/`Message`/`Bubble`/`Marker`.

**Skills:** `shadcn` (with the `info -c apps/web` edit), `vercel-react-best-practices`, and `vercel-composition-patterns`, all installed with `bunx skills add … -a claude-code --copy -y` and committed under `.claude/skills/`. No MCP.

**`apps/web/.oxlintrc.json`:**

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["eslint", "typescript", "unicorn", "oxc", "react", "jsx-a11y", "import"],
  "jsPlugins": ["@shadcn/lint"],
  "categories": { "correctness": "error" },
  "options": { "typeAware": true },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/exhaustive-deps": "error",
    "react/react-in-jsx-scope": "off",
    "react/only-export-components": ["warn", { "allowConstantExport": true }],
    "shadcn/no-restyle": ["error", { "allow": ["layout"] }],
    "shadcn/no-raw-colors": "error",
    "shadcn/no-arbitrary-values": "error",
    "shadcn/no-inline-styles": "error",
    "shadcn/no-unknown-classes": "error",
    "shadcn/require-static-classes": "error"
  },
  "overrides": [
    {
      "files": ["src/components/ui/**"],
      "rules": {
        "shadcn/no-restyle": "off",
        "shadcn/no-arbitrary-values": "off",
        "react/only-export-components": "off"
      }
    }
  ]
}
```

**`apps/web` check script:** `tsc -b && oxlint && oxfmt && vitest run`. This is #218's script: `oxlint` now also runs `@shadcn/lint`, reads `typeAware` from the config, and oxfmt sorts classes. **Root `bun run check`:** the four Python checks, each workspace's `check`, and `knip` once at the root (`knip.json` with the `ignoreIssues` entry above). New dev dependencies in `apps/web`: `@shadcn/lint`, `oxlint-tsgolint`. At the root: `knip`.

## Surprises

- Base UI has been shadcn's default since July 2026, and React Aria is a third base. Older guides and the monorepo template still say `radix-nova`.
- shadcn now ships chat components (`MessageScroller` and friends) and a skill rule that forbids hand-rolled stick-to-bottom hooks. This changes the chat-flow plan in web-app-stack.md.
- `cn` is its own package since 2026-09-03 (`export { cn } from "cn"`), replacing `clsx` + `tailwind-merge`.
- The official skill's context injection breaks in a monorepo root, and `-c apps/web` fixes it.
- The official sidebar block fails the official linter's `no-restyle` rule out of the box.
- react-doctor's licence restricts use in AI pipelines, which matters for an AI-agent project even though the tool is popular.

## Sources

- shadcn-ui/ui on `main`: `packages/shadcn/src/preflights/preflight-init.ts`, `packages/shadcn/package.json` (4.21.0), `packages/react/package.json`, `templates/vite-app/*`, `templates/vite-monorepo/apps/web/components.json`, `templates/vite-monorepo/packages/ui/src/styles/globals.css`, `skills/shadcn/{SKILL.md,cli.md,mcp.md,rules/chat.md,rules/base-vs-radix.md,agents/openai.yml}`, `skills/migrate-radix-to-base/`, `apps/v4/content/docs/changelog/{2026-03-cli-v4,2026-05-shadcn-eject,2026-06-chat-components,2026-07-base-ui-default,2026-07-react-aria,2026-09-cn}.mdx`
- https://ui.shadcn.com/docs/installation/vite, https://ui.shadcn.com/docs/skills, https://ui.shadcn.com/docs/changelog, https://ui.shadcn.com/docs/components/sidebar, https://ui.shadcn.com/llms.txt
- https://github.com/shadcn-ui/lint: `README.md`, `SETUP.md`, `docs/react.md`, `docs/evals.md`, `docs/rules/no-unknown-classes.md`, `LICENSE`; npm `@shadcn/lint` 0.2.0
- https://tailwindcss.com/docs/installation/using-vite
- https://oxc.rs/docs/guide/usage/linter/plugins.html, https://oxc.rs/docs/guide/usage/linter/rules.html, https://oxc.rs/docs/guide/usage/linter/js-plugins.html, https://oxc.rs/docs/guide/usage/linter/type-aware.html, https://oxc.rs/docs/guide/usage/formatter/sorting.html
- https://github.com/millionco/react-doctor: `README.md`, `LICENSE`, `packages/oxlint-plugin-react-doctor/README.md`; npm `react-doctor` 0.9.14
- https://github.com/vercel-labs/skills (README: options, agent paths), https://github.com/vercel-labs/agent-skills (`skills/react-best-practices`, `skills/composition-patterns`, `skills/web-design-guidelines`), https://github.com/anthropics/skills (`skills/frontend-design`)
- https://knip.dev/reference/plugins, https://knip.dev/reference/configuration, https://knip.dev/reference/known-issues
- npm registry on 2026-09-24: `shadcn` 4.21.0, `tailwindcss` 4.3.3, `@tailwindcss/vite` 4.3.3, `@base-ui/react` 1.8.0, `cn` 0.4.0, `oxlint` 1.85.0, `oxlint-tsgolint` 7.0.2003, `oxfmt` 0.70.0, `typescript` 7.0.2, `knip` 6.38.0, `eslint-plugin-tailwindcss` 4.4.0, `eslint-plugin-better-tailwindcss` 4.7.0, `@eslint-react/eslint-plugin` 5.20.8, `eslint-plugin-react-hooks` 7.1.1, `@biomejs/biome` 2.5.14, `ultracite` 7.12.0
- Scratch run: create-vite 9.2 `react-ts`, `shadcn init -b base -p nova`, `shadcn add sidebar-07 message-scroller message bubble`, `vite build`, `oxlint` 1.85.0 with `@shadcn/lint` 0.2.0 and `typeAware`, `react-doctor` 0.9.14 `--json --no-telemetry --no-supply-chain`, `knip` 6.38.0, on Node 24.13 under Windows
