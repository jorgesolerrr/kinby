# design-sync notes for kinby

Syncs `apps/web`'s shadcn components (Base UI, preset `base-nova`) to the Claude Design project in `config.json`.

## How the build works

- The web app ships no library build. `node .design-sync/build-ds.mjs` (cfg.buildCmd, run from the repo root) writes a gitignored pseudo-package at `apps/web/node_modules/.ds`: `index.ts` re-exporting every `src/components/ui/*.tsx` plus `kinby-mark.tsx`, tsc declarations under `types/` (with the `@/` alias rewritten to relative paths), and `kinby.css`.
- `kinby.css` is `.design-sync/kinby.css` compiled by the Tailwind CLI: the app's `index.css`, scanned over `src/components` and `.design-sync/previews`, plus `@source inline(...)` safelists so every kinby token exists as a utility (`bg-kin`, `text-ember`, `bg-primary/10`, ...). The fontsource `@font-face` rules are stripped; `cfg.extraFonts` ships the same faces with the files.
- Paths in `config.json` are relative to the pseudo-package (`apps/web/node_modules/.ds`), hence the `../../` prefixes.
- Run order on every sync: `node .design-sync/build-ds.mjs`, then `package-build.mjs --node-modules apps/web/node_modules` (or the driver).
- On Windows, `execSync` runs under cmd, so the prep script calls tools through `bun x`, never `node_modules/.bin/...`.

## Gotchas

- **A preview that uses a new utility class needs the CSS re-compiled.** `preview-rebuild.mjs` does not re-run Tailwind. After authoring, run `build-ds.mjs` and a full `package-build.mjs` again, or the class is missing and the cell collapses (seen: `w-32` on the Card Stat cell).
- `CardTitle` keeps `group-data-[size=sm]/card:text-sm` inside a small card, so a `text-2xl` className on it loses there. Put large numbers in their own element.
- Playwright: Chromium build 1243 is cached locally, which pins `playwright@1.63.0` in `.ds-sync`.
- Subcomponents (CardHeader, DialogContent, ...) ship as their own components with floor cards. Previews are authored for the family roots only.

- recharts ships inside the bundle as `Kinby.Recharts` (`build-ds.mjs` adds `export * as Recharts from "recharts"`; `componentSrcMap` keeps it off the card list). Previews and designs must draw charts with `Recharts.BarChart` etc. from `@kinby/web`, never `import ... from "recharts"`: a second copy breaks ChartContainer's size context and the chart draws 100x100 and empty.
- `kinby.css` safelists the standard sizing, spacing, grid and text scales so designs find common utilities. Arbitrary values (`w-[28rem]`) still exist only when a scanned file uses them.
- Overlays render open with `open` on the root and no `onOpenChange`; cardMode `single` with a viewport per overlay (config `overrides`). Modal autofocus shows a focus ring in captures; that's expected.
- `DropdownMenuContent` is `w-(--anchor-width)`: give it an explicit width or it shrinks to the trigger.
- Tooltip previews need their own `TooltipProvider` (the app's lives in `App.tsx`). Avoid the `tooltip` prop on `SidebarMenuButton` without one.
- Sidebar in a cell: `SidebarProvider className="relative min-h-0 overflow-hidden"` with a fixed height, and `Sidebar className="absolute h-full"`; the default container is `fixed h-svh`.
- A disabled Checkbox fades only inside a `Field` (Base UI renders a span, so `disabled:` never matches). A disabled Label fades only under `.group[data-disabled=true]`.
- Select and Combobox popups portal out of the cell; previews show the closed trigger with `defaultValue`. `Select` needs `items` for `SelectValue` to print the label.
- `grep -c '\.size-3'` also matches `.size-3\.5`; check class existence with `grep -F` and the escaped selector.

## Known render warns

- `[TOKENS_MISSING]` `--available-height`, `--anchor-width`, `--available-width`, `--transform-origin`: Base UI sets them at runtime on popups. `--sdm-tbg`, `--tw`, `--streamdown-caret`: Streamdown's classes, scanned in by `index.css`; not part of the DS.
- `[FONT_MISSING]` "Cambria" (`--font-serif`): Tailwind's default serif stack. kinby never uses `font-serif`.

## Re-sync risks

- **Pseudo-package drift.** `build-ds.mjs` derives the entry from whatever `.tsx` files sit in `src/components/ui`. A new ui component appears automatically. A file that stops being a component (a hook, a helper) shows up as a card: exclude it with `componentSrcMap`.
- **Tailwind CLI pin.** The CSS compiles with `@tailwindcss/cli@4.3.3` via `bunx` (network fetch on first run). Bump it with the app's `tailwindcss`; a mismatch can drop or change utilities.
- **Previews tied to app code.** Several previews port compositions from `thread-panel.tsx`, `new-skill.tsx`, `hub-secrets-section.tsx`, `nav-user.tsx`, `library-page.tsx` and `stats-*.tsx`. They don't import those files, so a redesign there won't break a card, but the cards can go stale against the real UI.
- **Sidebar positioning hack.** The Sidebar preview overrides `fixed h-svh` with `absolute h-full`. If `sidebar.tsx` changes its container classes, recheck that card.
- **Only the 35 family roots are authored.** The other 162 sub-part cards are floor cards by design.
- **Playwright pin.** `playwright@1.63.0` matches the cached Chromium 1243. A new machine or cleared cache needs a matching pair.
