# Instance module: init, manifest, discovery

| | |
|---|---|
| **Status** | built |
| **Source** | grilling on #4 (resolved), spec #13, slice tickets #14–#20; 2026-08-24. The code at the stamped commit is the second source of truth. |
| **Stamped at** | `6e18454` (paths and symbols are true at this commit) |
| **Owner** | Jorge Soler |

## At a glance *(explanation)*

You run `kinby init <dir>` and get a directory you can read end to end, then `kinby instance show` and `kinby run` find that directory the way git finds a repo and print what kinby sees. The solution is one package, `kinby.instance`, that owns three things: the canonical file names of an instance (`layout.py`), the parse and validation of `kinby.toml` into frozen dataclasses (`manifest.py`), and the four-rule lookup that turns "where am I" into "which instance" (`discovery.py`). The decision that shapes everything else is D1: an instance is one directory, the workspace lives under it, and runtime state lives in its `.state/`, so tarring the directory moves the instance and one container image serves every instance.

## Problem *(explanation)*

Before this feature, a self-hoster had nothing to install and nothing to point kinby at. The repository held a learning project (`agent/`, `main.py`, a package named `agent`), there was no notion of an instance on disk, and no command created or located one. Every later piece (the loop, the gate, memory, evals) needs a place to plug into, and that place did not exist.

## Solution *(explanation)*

`pip install kinby` gives you a `kinby` command. `kinby init <dir>` writes a starter instance where every extension point is a real file with a comment saying what it is for, and refuses to touch a directory that already holds `kinby.toml`. `kinby.toml` has one required setting, `[models].main`, and never holds a secret. `kinby instance show` prints the resolved instance: id, path, which discovery rule matched, models, workspace, conventions, state dir. `kinby run` does the same, applies `--model` for the session without editing the file, and stops with "The agent loop is not yet available." A bad manifest fails with a message that names the key. A generic Docker image mounts any instance at `/instance`.

## Decision log *(explanation)*

| # | Decision | Chosen | Rejected | Why |
|---|---|---|---|---|
| D1 | Shape of an instance | One self-contained directory: `kinby.toml`, behavior files, `memory/`, `workspace/` under it, gitignored `.state/` for runtime data; `state_dir` is a manifest override | A `.kinby/` directory inside the user's repo; runtime state outside the instance by default | A `.kinby/` per repo fragments memory and drops databases into the user's repo. State outside the instance breaks "tar the directory and you have moved the instance" and needs a second container mount. (#4, ADR 0001) |
| D2 | Starting point | A fresh `src/kinby` scaffold; `agent/` and `main.py` deleted in the same change | Growing the learning-project code into the product | The learning project was a spike, not a seed. One package to read. (#4, #14) |
| D3 | Manifest format and secrets | TOML parsed by the standard library (`tomllib`); secrets come only from the environment, with `<instance>/.env` loaded for local use | Secrets as manifest keys | A manifest with no secrets is committable, and TOML is inspectable without executing anything. A container and a laptop get secrets the same way. (#13) |
| D4 | Minimal instance versus what `init` writes | Minimal is `kinby.toml` with `[models].main`; `init` writes every default out as a real file with an explanatory comment | `init` writes only the minimal file | The user learns what is editable by looking at files, not docs. Minimal is a validity rule, not a template. (#4, #15) |
| D5 | Instance identity | `id` in the manifest, written by `init` as a slug of the directory name; the path is location, not identity | The path as identity | Moving the directory must not rename the instance. (#4) |
| D6 | Model settings | `[models].main` required in `provider:model` form; `recap` defaults to `main`; `embed` optional; the manifest is re-read at every turn boundary through `reload_manifest`; `kinby run --model` overrides `main` for one session without touching the file | Restarting to switch models; the agent rewriting its own manifest | Switching models never means restarting. Agent self-modification is fog until something needs it. (#4, #19) |
| D7 | Discovery | Fixed precedence: explicit directory or `--instance`, then `KINBY_INSTANCE`, then walk up from cwd for `kinby.toml`, then `~/.kinby/default/`; a directory is an instance only when it holds `kinby.toml`; nothing is auto-created; the matching rule is part of the result | Auto-creating an instance when none resolves; a workspace-to-instance registry | Silent creation puts instances in the wrong place. Walk-up already finds the instance from inside `workspace/`; a symlinked workspace uses `KINBY_INSTANCE`. (#4, #17, ADR 0002) |
| D8 | Workspace conventions | Off by default; explicit lists `instructions = ["AGENTS.md"]` and `skills = [".agents/skills"]` resolved against the workspace; only entries that exist are reported, in declared order; the instance never loads tools from the workspace; instance behavior wins on conflict | Vendor-aware auto-detection of convention files; loading workspace tools | Explicit lists let the user add `CLAUDE.md` without kinby knowing every vendor. Cloning an untrusted repo must not hand the agent new executable capabilities. (#4, #18) |
| D9 | Unknown and malformed manifest keys | Unknown keys are errors; every `ManifestError` names the offending key (`models.main: required`, `workspace.conventions.enabled: must be a boolean`) | Ignoring unknown keys | A typo in a key must fail loudly, and the user fixes the file instead of guessing. (#13, #16) |
| D10 | Container contract | One generic image; the instance is a single volume at `/instance`; `KINBY_INSTANCE=/instance` is baked in; secrets by environment; the entrypoint, not the instance module, owns clone-on-first-boot | A per-instance image; git cloning inside `kinby.instance` | One image serves every instance. The instance module stays free of a git dependency and only resolves and reports the workspace. (#4, #20, ADR 0003) |
| D11 | Module boundaries and test seam | `kinby.instance` imports no CLI and no LangGraph code; tests drive `kinby.cli.main` in-process and assert only on exit code, stdout, stderr, and files | Unit tests that import discovery or manifest internals | The instance module is the seam a server wraps next. Tests at the CLI survive any refactor of the internals. (#13, #16) |
| D12 | `kinby run` before the loop exists | Registered now; resolves, validates, applies `--model`, prints the `show` summary, then exits 1 with the loop-unavailable message | Adding `run` together with the loop | Registering the command fixes the CLI contract the runtime seam later fills. (#13, #19) |

## User stories *(reference)*

1. As a self-hoster, I want `pip install kinby` or `uv add kinby` to give me a `kinby` command, so that the project name, package name, import name, and CLI are the same word.
2. As a self-hoster, I want `kinby init <dir>` to create an instance directory I can read end to end, so that I learn what is editable by looking at files, not docs.
3. As a self-hoster, I want `kinby init` to refuse to overwrite an existing instance, so that I cannot wipe one by accident.
4. As a self-hoster, I want `kinby init` to write an `id` derived from the directory name into the manifest, so that moving the directory later does not rename my instance.
5. As a self-hoster, I want a `kinby.toml` with a single required setting, `[models].main`, so that the minimal instance is one short file.
6. As a self-hoster, I want to name separate models for `recap` and `embed`, so that the recap subagent and the embedder are not forced onto my main model.
7. As a self-hoster, I want `recap` to default to `main` when omitted and `embed` to be optional, so that I am not asked for what I do not use.
8. As a self-hoster, I want to edit `[models].main` while the agent is running and have the next turn use it, so that switching models never means restarting.
9. As a self-hoster, I want `kinby run --model provider:model` to override the manifest for one session without touching the file, so that I can try a model without committing to it.
10. As a self-hoster, I want to set a persona name in the manifest, so that my agent has its own name distinct from "kinby".
11. As a self-hoster, I want the manifest to never contain API keys, so that I can commit my instance directory to git.
12. As a self-hoster, I want secrets read from environment variables, and from a `.env` inside the instance for local use, so that a container and a laptop get secrets the same way.
13. As a self-hoster, I want `SYSTEM.md` to be the behavior prompt of the instance, so that changing behavior means editing one markdown file.
14. As a self-hoster, I want `permissions.toml`, `tools/`, `skills/`, `routines/`, and `memory/profile.md` written by `kinby init` with explanatory defaults, so that every extension point is visible even though only the manifest is required.
15. As a self-hoster, I want everything kinby writes at runtime to go under `.state/`, with a `.gitignore` that excludes it, so that my instance directory is committable without thinking.
16. As a self-hoster, I want to relocate runtime state with a `state_dir` manifest setting, so that an instance whose workspace is synced elsewhere does not carry databases with it.
17. As a developer using kinby on a repo, I want my repo to live under the instance as `workspace/`, so that kinby never writes its own files into my repo.
18. As a developer, I want `[workspace].path` to accept an absolute path, so that on my laptop the instance can point at a repo I already have checked out.
19. As a developer, I want `[workspace].source` to hold a git URL, so that a container can clone the workspace on first boot when `workspace/` is empty.
20. As a developer, I want a toggle to let the instance read my repo's `AGENTS.md` and `.agents/skills`, so that project conventions apply without copying them into the instance.
21. As a developer, I want those convention sources to be explicit lists, so that I can add `CLAUDE.md` or another file without kinby knowing every vendor.
22. As a developer, I want instance behavior to take precedence over repo conventions, and instance skills to shadow same-named repo skills, so that my persona is never overridden by a project file.
23. As a developer, I want kinby to never load tools from the workspace, so that cloning an untrusted repo cannot give the agent new executable capabilities.
24. As a self-hoster, I want `kinby run <dir>` and `--instance <dir>` to pick an instance explicitly, so that scripts never depend on my cwd.
25. As an operator running kinby in a container, I want `KINBY_INSTANCE` to select the instance, so that the image needs no per-instance configuration.
26. As a developer, I want to `cd` anywhere inside an instance, including inside `workspace/`, type `kinby …`, and have it find the instance by walking up for `kinby.toml`, so that it behaves like git.
27. As a self-hoster, I want `~/.kinby/default/` used when nothing else resolves, so that one instance on my laptop needs no flags.
28. As a self-hoster, I want a clear error naming `kinby init` when no instance resolves, so that kinby never silently creates one in the wrong place.
29. As a self-hoster, I want `kinby instance show` to print the resolved instance (id, path, how it was resolved, models, workspace, state dir, conventions), so that I can see what kinby sees.
30. As a self-hoster, I want an invalid manifest (missing `models.main`, malformed model string, unknown key) to fail with a message naming the key, so that I fix the file instead of guessing.
31. As an operator, I want a generic container image that mounts the instance at `/instance` with `KINBY_INSTANCE` pre-set, so that one image serves every instance.
32. As a contributor, I want a `src/` layout with `tests/`, `evals/`, `examples/instances/`, and `docs/`, so that tests run against the installed package and examples are real instances.
33. As a contributor, I want `examples/instances/` to hold a minimal instance and a coding-agent instance, so that both shapes are documented by working directories.
34. As a contributor, I want the old `agent/` package and `main.py` removed in the same change, so that there is one package to read.
35. As a future server author, I want the instance module to be importable and free of CLI concerns, so that a server can resolve instances the same way the CLI does.

## Bird's-eye flow *(reference)*

![Data flow of the instance module: the CLI either writes a starter instance through init_instance or resolves one through discover_instance, load_instance reads kinby.toml and .env from disk, _parse_manifest validates them into a Manifest, and the CLI prints the resolved Instance.](diagrams/birds-eye.svg)

Source: [`birds-eye.html`](diagrams/birds-eye.html)

Prose walkthrough, one step per arrow:

1. `main` parses `argv`. For `init` it calls `init_instance(directory, model)`. For `instance show`, `run`, and `thread …` it computes the explicit directory (`--instance` beats the positional argument) and the session `model_override` (`run --model` only).
2. `init_instance` writes the starter tree into the instance directory and returns its resolved path (D4). It raises `InstanceExistsError` when `kinby.toml` is already there.
3. `main` calls `discover_instance(directory, model_override=…)`. The four rules run in order (D7) and the first hit names its `matching_rule`.
4. `discover_instance` calls `load_instance(path, matching_rule=…, model_override=…)`.
5. `load_instance` reads the instance directory: it loads `.env` into the environment without overriding existing variables (D3), then parses `kinby.toml` with `tomllib`.
6. `_parse_manifest` validates the raw table into a `Manifest` (D6, D8, D9), applying the session override to `models.main`. This is the focal step: every user-facing manifest error originates here.
7. `main` receives an `Instance` and `_print_instance` renders it. For `run`, it then prints the loop-unavailable line and exits 1 (D12).

## Module map *(reference)*

![Dependency graph of the instance module: cli/main.py calls discovery.py and init.py, discovery.py calls manifest.py, and all three plus manifest.py share the leaf modules layout.py, dataclasses.py, and errors.py, with tomllib and python-dotenv as manifest.py's only external dependencies.](diagrams/module-map.svg)

Source: [`module-map.html`](diagrams/module-map.html)

The three leaf modules are drawn as one node because every consumer imports at least two of them. `discovery.py` also imports `Instance` from `dataclasses.py` for its return type.

### Interfaces

Everything below is re-exported from `kinby.instance`; clients import the package, never the submodules.

- `init_instance(directory: Path, model: str | None = None) -> Path` — writes the starter tree at `directory`, creating it if absent, and returns the resolved path. Raises `InstanceExistsError` when `kinby.toml` already exists. Called by `main` for `kinby init`.
- `discover_instance(directory: Path | None = None, *, model_override: str | None = None) -> Instance` — runs the four discovery rules (D7) and loads the first hit. Raises `InstanceNotFoundError` when none matches, `ManifestError` when the hit does not load. Called by `main` for `instance show`, `run`, and `thread …`.
- `load_instance(directory: Path, *, matching_rule: MatchingRule = "explicit directory", model_override: str | None = None) -> Instance` — loads `.env`, parses and validates `kinby.toml`, and tags the result with `matching_rule`. Called by `discover_instance` and by tests.
- `reload_manifest(instance: Instance, *, model_override: str | None = None) -> Manifest` — re-reads the manifest of an already resolved instance, keeping its `matching_rule` and reapplying the session override (D6). For the loop to call at turn boundaries; no caller yet (Q1).
- `Instance`, `Manifest`, `Models`, `Workspace`, `Conventions`, `Memory` — frozen dataclasses, see Data shapes.
- `InstanceExistsError`, `InstanceNotFoundError`, `ManifestError` — the three exceptions `main` translates into a stderr line and exit 1.
- `kinby.instance.layout` — the string constants for every name in an instance directory (`MANIFEST_NAME`, `STATE_DIR`, `WORKSPACE_DIR`, …). Not re-exported; the only module allowed to spell a path.

## Ground-level flows *(reference)*

### Flow: init

![Sequence for kinby init: main calls init_instance, which checks the instance directory for kinby.toml, either raises InstanceExistsError that main prints to stderr with exit 1, or writes the starter tree and returns the resolved path that main prints with exit 0.](diagrams/flow-init.svg)

Source: [`flow-init.html`](diagrams/flow-init.html)

1. `main(["init", dir, "--model", m])`: input `argv`, output the call `init_instance(Path(dir), model=m)`.
2. `init_instance` checks `<dir>/kinby.toml`: input the path, output a boolean from `Path.is_file`.
3. When the manifest exists, `init_instance` raises `InstanceExistsError("instance already exists: <path>")` before creating anything. `main` prints it to stderr and returns 1.
4. Otherwise `init_instance` creates the directory and writes, in order, `kinby.toml` (with `id = _slugify(dir.name)` and `main = m or "provider:model"`), `SYSTEM.md`, `permissions.toml`, `memory/profile.md`, `.gitignore`, `tools/README.md`, `skills/README.md`, `routines/README.md`, then creates empty `workspace/` and `.state/`. Every written file starts with a comment.
5. `init_instance` returns `directory.resolve()`; `main` prints `Created instance at <path>` and returns 0. The model placeholder is not validated here (D4).

### Flow: show and run

![Sequence for kinby instance show and kinby run: main calls discover_instance, which tries the four rules in order and calls load_instance on the first hit; load_instance loads .env and kinby.toml from disk and hands the table to _parse_manifest, which returns a Manifest; the Instance flows back to main, which prints it.](diagrams/flow-show.svg)

Source: [`flow-show.html`](diagrams/flow-show.html)

1. `main` computes `directory = Path(--instance or positional) or None` and `model_override = --model` when the command is `run`, else `None`. Output: the call `discover_instance(directory, model_override=…)`.
2. `discover_instance` tries the rules in order (D7): an explicit `directory`; `KINBY_INSTANCE`; `cwd` and each of its parents, testing `candidate / "kinby.toml"`; `~/.kinby/default`. Output: a path plus a `MatchingRule` literal. When nothing matches it raises `InstanceNotFoundError("No kinby instance found. Run `kinby init <directory>` first.")`.
3. `discover_instance` calls `load_instance(path, matching_rule=rule, model_override=…)`.
4. `load_instance` calls `load_dotenv(<instance>/.env, override=False)` (D3). Output: environment variables added, existing ones kept.
5. `load_instance` opens `<instance>/kinby.toml` in binary mode and calls `tomllib.load`. A missing file becomes `ManifestError("kinby.toml: not found in <path>")`; a parse error becomes `ManifestError("kinby.toml: <decoder message>")`. Output: a `dict[str, Any]`.
6. `load_instance` calls `_parse_manifest(instance_path, values, model_override=…)`. Output: a `Manifest`, or a `ManifestError` naming the key (D9).
7. `load_instance` returns `Instance(path, manifest, matching_rule)` to `discover_instance`, which returns it to `main`.
8. `main` calls `_print_instance(instance)`, which writes the summary to stdout. For `run`, `main` then prints `The agent loop is not yet available.` and returns 1; for `show` it returns 0. Any `InstanceNotFoundError` or `ManifestError` from steps 2–6 is printed to stderr and `main` returns 1.

### Data shapes

`kinby.toml`, every key. Unknown keys at any level are errors.

| Key | Type | Required | Default | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | non-empty |
| `persona_name` | string | no | `None` | non-empty when present |
| `state_dir` | path | no | `.state` | relative paths resolve against the instance |
| `[models].main` | string | yes | — | `provider:model`, no whitespace, exactly one colon |
| `[models].recap` | string | no | `main` after override | same form |
| `[models].embed` | string | no | `None` | same form |
| `[workspace].path` | path | no | `workspace` | relative paths resolve against the instance; absolute pass through |
| `[workspace].source` | string | no | `None` | not validated as a URL |
| `[workspace.conventions].enabled` | bool | no | `false` | must be a boolean |
| `[workspace.conventions].instructions` | list of string | no | `["AGENTS.md"]` | non-empty strings; resolved against the workspace; only existing files kept |
| `[workspace.conventions].skills` | list of string | no | `[".agents/skills"]` | non-empty strings; resolved against the workspace; only existing directories kept |
| `[memory]` | table | no | `{}` | accepted empty; no keys allowed yet |

The loaded shapes, from `src/kinby/instance/dataclasses.py`. All frozen.

```python
MatchingRule = Literal["explicit directory", "KINBY_INSTANCE", "walk-up", "home default"]

@dataclass(frozen=True)
class Models:      main: str; recap: str; embed: str | None
@dataclass(frozen=True)
class Conventions: instructions: tuple[Path, ...]; skills: tuple[Path, ...]
@dataclass(frozen=True)
class Workspace:   path: Path; source: str | None; conventions: Conventions
@dataclass(frozen=True)
class Memory:      pass                      # reserved, shaped by the memory spec
@dataclass(frozen=True)
class Manifest:    id: str; persona_name: str | None; state_dir: Path
                   models: Models; workspace: Workspace; memory: Memory
@dataclass(frozen=True)
class Instance:    path: Path; manifest: Manifest; matching_rule: MatchingRule
```

The session override (D6) applies inside `_parse_manifest`: the configured `main` is validated first, then replaced by `model_override` when one is given, and `recap` defaults to the value after the override. `reload_manifest` repeats the whole load with the same override, so an edit to `kinby.toml` shows up and the override still wins.

The starter tree `init_instance` writes (D4):

```
<dir>/
  kinby.toml          # id = "<slug>", [models] main = "<--model or provider:model>"
  SYSTEM.md           # behavior prompt
  permissions.toml    # comment only; the gate is not built
  memory/profile.md   # comment only
  tools/README.md     # "kinby does not load tools from the workspace"
  skills/README.md
  routines/README.md
  workspace/          # empty
  .state/             # empty
  .gitignore          # .state/ and .env
```

`_slugify` NFKD-normalizes the directory name, strips combining marks, lowercases, replaces every run of non `[a-z0-9]` with `-`, trims dashes, and falls back to `instance` when nothing is left. `My Agent` becomes `my-agent`.

`kinby instance show` output, in order: `id`, `persona name` (when set), `path`, `matching rule`, `models:` with `main`, `recap`, `embed` (`not configured` when absent), `workspace: <path> (present|missing)`, `source` (when set), `conventions:` with `instructions:` and `skills:` (only when at least one exists), `state dir`.

Container contract (D10), from `Dockerfile` and `docs/container.md`: `python:3.14-slim`, `pip install .`, `ENV KINBY_INSTANCE=/instance`, `VOLUME ["/instance"]`, `ENTRYPOINT ["kinby"]`, `CMD ["run"]`. The entrypoint does not clone a workspace yet.

## File map *(reference)*

Stamped at `6e18454`. Actions describe what the build did relative to the tree before #14; every `create` path exists at the stamp. Regenerate the existence check with `git ls-files src/kinby/instance src/kinby/cli tests examples Dockerfile docs/container.md docs/adr pyproject.toml`.

| Path | Action | What changes | Flow |
|---|---|---|---|
| `pyproject.toml` | create | Package `kinby`, src layout, script `kinby = "kinby.cli:main"`, dependency `python-dotenv`, dev dependencies `pytest`, `ruff` | — |
| `uv.lock` | create | Lockfile | — |
| `agent/`, `main.py` | delete | The learning project (D2) | — |
| `src/kinby/__init__.py` | create | Package docstring | — |
| `src/kinby/core/__init__.py`, `src/kinby/memory/__init__.py`, `src/kinby/plugins/__init__.py` | create | Docstring stating each package's responsibility (`core` has since gained the runtime seam) | — |
| `src/kinby/cli/__init__.py` | create | Re-exports `main` | — |
| `src/kinby/cli/main.py` | create | `argparse` tree for `--version`, `init`, `instance show`, `run`; `_add_instance_selector`; `_print_instance`; error-to-exit-code translation (`thread …` was added later by #30) | init, show and run |
| `src/kinby/instance/__init__.py` | create | Public surface: the four functions, six dataclasses, three errors | — |
| `src/kinby/instance/layout.py` | create | Canonical names: `MANIFEST_NAME` … `GITIGNORE_NAME` | init, show and run |
| `src/kinby/instance/dataclasses.py` | create | `MatchingRule`, `Models`, `Conventions`, `Workspace`, `Memory`, `Manifest`, `Instance` | show and run |
| `src/kinby/instance/errors.py` | create | `InstanceExistsError`, `InstanceNotFoundError`, `ManifestError(ValueError)` | init, show and run |
| `src/kinby/instance/init.py` | create | `init_instance`, `_slugify`, `PLACEHOLDER_MODEL` | init |
| `src/kinby/instance/manifest.py` | create | `load_instance`, `reload_manifest`, `_parse_manifest`, `_parse_conventions`, key validators | show and run |
| `src/kinby/instance/discovery.py` | create | `discover_instance` with the four rules | show and run |
| `tests/test_cli.py` | create | `--version` prints the package version (the pattern every later test follows) | — |
| `tests/test_init.py` | create | Tree, refusal, slug, `--model`, placeholder not validated | init |
| `tests/test_instance_show.py` | create | Manifest reporting, key errors, paths, `.env`, each discovery rule and their precedence, conventions | show and run |
| `tests/test_run.py` | create | `--model` override shown with the file untouched; `reload_manifest` reads edits and reapplies the override | show and run |
| `tests/test_examples.py` | create | Both examples load through `instance show` | show and run |
| `tests/test_container.py` | create | Image config and a mounted `instance show` (skipped without Docker) | show and run |
| `evals/.gitkeep` | create | Directory for Inspect AI tasks (#2) | — |
| `examples/instances/minimal/kinby.toml` | create | `id` and `[models].main` only | — |
| `examples/instances/coding-agent/kinby.toml` | create | `persona_name`, `[workspace]`, conventions enabled | — |
| `examples/instances/coding-agent/workspace/AGENTS.md`, `…/workspace/.agents/skills/README.md` | create | The conventions the example reports | — |
| `Dockerfile` | create | Generic image per D10 | — |
| `docs/container.md` | create | The container contract and the clone-on-first-boot boundary | — |
| `docs/adr/0001-instance-is-a-directory-workspace-lives-under-it.md` | create | D1 | — |
| `docs/adr/0002-instance-discovery-uses-fixed-precedence.md` | create | D7 | — |
| `docs/adr/0003-container-entrypoint-owns-workspace-cloning.md` | create | D10 | — |
| `CONTEXT.md` | modify | Terms *instance*, *instance discovery*, *manifest*, *workspace*, *conventions* | — |

## Testing *(reference)*

- **Seams**: one. `kinby.cli.main([...])` invoked in-process with `tmp_path`, `capsys`, and `monkeypatch` for cwd, `HOME`, `USERPROFILE`, and `KINBY_INSTANCE` (D11). No test imports `discovery` or `manifest` internals. The one exception is `reload_manifest`, which has no CLI caller yet and is tested through the public `kinby.instance` surface in `tests/test_run.py`.
- **What a good test looks like here**: write a `kinby.toml` into a temp directory, call `main`, then assert on the exit code, on lines of stdout, on stderr, and on files on disk. A test that survives renaming every private helper in `manifest.py` is at the right altitude.
- **Prior art**: none before this feature. This feature set the pattern; `tests/test_thread_cli.py` (#30) follows it.
- **Cases**, mapped to user stories:
  1. `init` writes the documented tree and every file starts with a comment. (2, 13, 14, 15)
  2. `init` on a directory with `kinby.toml` exits non-zero, writes to stderr, and changes nothing. (3)
  3. `init` writes `id` as the slug of the directory name. (4)
  4. `init --model` lands in the manifest; without it the placeholder is written and not validated. (5, 9)
  5. `instance show <dir>` reports id, persona name, path, `explicit directory`, models with `recap` defaulted, workspace `present` with `source`, and a relative `state_dir` resolved against the instance. (6, 7, 10, 16, 29)
  6. Missing `models.main`, a malformed model string (`main`, `recap`, `embed`), and an unknown key at each level fail naming the key. (30)
  7. A missing default workspace is reported as `missing`; absolute `state_dir` and `workspace.path` pass through. (17, 18)
  8. `<instance>/.env` is loaded and never overrides an existing environment variable. (11, 12)
  9. Each discovery rule alone: `KINBY_INSTANCE`, walk-up from `workspace/sub/dir`, home default. (25, 26, 27)
  10. Precedence: `--instance` beats `KINBY_INSTANCE`; `KINBY_INSTANCE` beats walk-up and home default; walk-up beats home default. (24)
  11. No instance: exit non-zero, stderr names `kinby init`, `~/.kinby` is not created. (28)
  12. Conventions: nothing listed when disabled or absent; defaults listed when enabled; custom lists in declared order; missing entries omitted; non-list and non-boolean values fail naming the key; a `tools/` directory in the workspace is never listed. (20, 21, 23)
  13. `run --model` shows the override, leaves the file byte-identical, prints the loop-unavailable line, and exits 1. (8, 9)
  14. `reload_manifest` reads an edited manifest and reapplies the override to `main` and `recap`. (8)
  15. Both `examples/instances/*` load through `instance show`; only `coding-agent` lists conventions. (33)
  16. The built image has `KINBY_INSTANCE=/instance`, volume `/instance`, entrypoint `kinby`, command `run`, and `instance show` on a mounted example reports `matching rule: KINBY_INSTANCE`. (31)

## Out of scope *(reference)*

- The agent loop, context manager, gate, and usage channel: the runtime seam blueprint (`docs/blueprints/runtime-seam/`) and #7.
- Tool and skill loading, hot-plugging, the default tool package: plugin contracts (#6).
- Memory feeds and the contents of `.state/` beyond the directory existing: the memory facade (#8). `[memory]` stays an empty table until then.
- The routines file format (#11) and `mcp.json` handling.
- Git cloning of the workspace inside kinby (ADR 0003), several workspaces per instance, a workspace-to-instance registry.
- The agent rewriting its own manifest (`set_model`).
- Consuming conventions: this feature only resolves and reports them; the loop reads them later, with instance behavior first and instance skills shadowing same-named workspace skills (D8).
- Any UI, server, or channel.

## Open questions *(reference)*

The spec left these open and the code does not settle them. The agent building on this module stops and asks before acting on any of them.

1. **Who calls `reload_manifest` at turn boundaries.** D6 says the manifest is re-read at every turn boundary "by whoever owns the loop". The runtime seam blueprint does not list a caller. Options: `Turns.start` reloads before each turn; `LangGraphRunner` reloads when it builds the model; the REPL reloads per line. Recommended default: the turn runner, since it is the one that needs `models.main`.
2. **When `embed` becomes required.** The manifest accepts `embed` as optional and nothing checks it. Options: the memory spec (#8) validates it when a feed that needs it is enabled; `show` warns when a feed is enabled without it. Recommended default: leave it to #8, as the spec says.
3. **Shape of `[memory]`.** Reserved and rejected non-empty today. Options: feed toggles per #4; a table per feed. Recommended default: decide in #8; this module only needs to keep rejecting unknown keys.

## Glossary *(reference)*

- **Instance** — one kinby deployment: a directory that owns its behavior configuration, memory, and transcripts; here, any directory holding `kinby.toml`.
- **Instance discovery** — the ordered lookup that selects an existing instance when a command does not name one; the matching rule is part of the result.
- **Manifest** — `kinby.toml`, the portable description of an instance's identity and configuration; no secrets, no runtime state.
- **Workspace** — the directory holding the user's own work that an instance acts on; lives under the instance and is never written to by kinby on its own behalf.
- **Conventions** — the workspace's instruction files and skill directories an instance may read as extra behavior sources; named explicitly in the manifest.
- **Persona name** — the per-instance display name, `persona_name` in the manifest; distinct from *kinby*, the project.
- **Session** — one run of the agent loop against a thread; the unit `--model` overrides for.
- **Turn** — one user-to-agent cycle; the boundary at which the manifest is re-read.
