# How Warp models and edits a factory's steps

Research for #1384 ("Each step of a factory has a view the user edits it from"). The question: how Warp models a multi-agent factory and lets a user configure each unit, so the grilling can choose what to copy.

Sources were read on 2026-11-19. Most come from docs.warp.dev, fetched as the `_llms-txt` bundles ([factories.txt](https://docs.warp.dev/_llms-txt/factories.txt), [automation-platform.txt](https://docs.warp.dev/_llms-txt/automation-platform.txt), [agents.txt](https://docs.warp.dev/_llms-txt/agents.txt)) from docs deployment `dpl_BJWmwf8Y7XoNJN9pocQx5MxejteH`. The pages carry no dates of their own. The definition schema is `v1alpha1`. The other sources are Warp's launch post ([Zach Lloyd, 2026-08-18](https://warp.dev/blog/open-infrastructure-for-building-a-software-factory)) and the [warp-factory-examples](https://github.com/warpdotdev/warp-factory-examples) repo. Any claim without a primary source is marked **unverified**.

## 1. What the product is today

**Warp Factories** launched on 2026-08-18 ([blog](https://warp.dev/blog/open-infrastructure-for-building-a-software-factory)). It is now the product, and Oz is the legacy layer under it. The Oz CLI is "a supported legacy interface… For new multi-stage development workflows, use Warp Factories" ([Oz CLI agent profiles](https://docs.warp.dev/agents/cli/oz-cli/agent-profiles/)). The Oz web app, CLI and SDKs remain for standalone cloud-agent workflows ([Transitioning from Oz](https://docs.warp.dev/platform/transitioning-from-oz/)). Every worker run in a factory is "an ordinary cloud agent run" on the Automation Platform ([Factories overview](https://docs.warp.dev/factories/)).

The units, from [Factories overview](https://docs.warp.dev/factories/), [About Blueprints](https://docs.warp.dev/factories/blueprints/) and [How Warp Factories work](https://docs.warp.dev/factories/how-factories-work/):

| Unit | What it is |
| --- | --- |
| **Factory** | A versioned tree of YAML and Markdown rooted at `factory.yaml`. It holds the repositories, workers, automations, runners, routers, skills, MCP servers, scorers, benchmarks and webhooks ([Definitions as code](https://docs.warp.dev/factories/factory-as-code/)). There is one per group of repositories that ship together ([Sizing](https://docs.warp.dev/factories/#sizing-a-factory)). |
| **Work item** (also called a task) | One request, such as an issue, a Slack thread or a PR. It keeps its identity from intake to handoff. |
| **Blueprint** | The workflow inside `factory.yaml`: `blueprint.start.to` plus `blueprint.stages`. |
| **Stage** | A unit of work with its own `worker`, `inputs`, `prompt`, `outputs`, `gates` and `to`. A stage can be a group, with its own `start` and `stages` ([Stages](https://docs.warp.dev/factories/blueprints/stages/)). |
| **Worker** (formerly "agent") | An agent configured for the factory: `workers/<name>/worker.md`, with frontmatter for settings and a body for shared instructions. It sets the model, harness, runner, environment, MCP servers, secrets and network policy ([Worker file](https://docs.warp.dev/factories/factory-as-code/#agentsnameagentmd)). |
| **Foreman** | The one worker that drives the top-level Blueprint, talks to the requester, dispatches stages to workers, and judges the transitions ([Workers](https://docs.warp.dev/factories/factory-agents/)). |
| **Transition** | A stage's `to`. It is either one stage or `end`, or an ordered list of `{stage, when}` where `when` is **prose** the coordinating LLM judges. "Conditions are not Boolean expressions evaluated by a rules engine" ([Transitions](https://docs.warp.dev/factories/blueprints/routes/)). |
| **Gate** | A named question under a stage (`prompt`, `outputs`, optional prose `when`). The stage stays open until a person answers ([Gates](https://docs.warp.dev/factories/blueprints/gates/)). |
| **Run** | One agent execution, with states `QUEUED` → `INPROGRESS` → `SUCCEEDED`/`FAILED`, plus `BLOCKED`. A work item spans several runs, with child runs grouped under their parent ([Dashboard: inspect runs](https://docs.warp.dev/factories/factory-dashboard/#inspect-runs), [Agent & run endpoints](https://docs.warp.dev/factories/api-and-sdk/)). |
| **Automation** | `automations/<name>/automation.md`, which holds triggers (schedule, GitHub/Linear/Slack/Jira event, webhook) and targets either a worker or a Blueprint `stage` ([Automations](https://docs.warp.dev/factories/factory-as-code/#automationsnameautomationmd)). |
| **Environment / runner** | The environment is the repos, toolchain and setup commands. The runner is the OS, arch, instance size and image ([Configuring workers](https://docs.warp.dev/factories/factory-agents/creating-custom-agents/#change-settings-for-one-dispatch)). |
| **Scorer / Self-improvement / Benchmark** | An LLM judge classifies completed runs. Self-improvement opens PRs against code or against the factory definition. Benchmarks compare configurations on fixed tasks ([Measure and improve](https://docs.warp.dev/factories/measure-and-improve/)). |

The default factory ships three workers: Foreman, Lifecycle worker and Researcher ([Workers](https://docs.warp.dev/factories/factory-agents/#the-default-workers)). The examples repo's `00-warp-default-agents` lists five ([examples README](https://github.com/warpdotdev/warp-factory-examples)), so the docs and the examples disagree.

The legacy local unit is the **Agent Profile**: name, base model, a planning model, a per-action autonomy level (Agent decides / Always ask / Always allow / Never), command allow and deny lists, and MCP allow and deny lists. It is edited in the app under **Settings > Agents > Profiles**. "New profiles will copy" the default profile's settings, and profiles sync to every host ([Agent Profiles](https://docs.warp.dev/agents/capabilities/agent-profiles-permissions/), [Oz CLI profiles](https://docs.warp.dev/agents/cli/oz-cli/agent-profiles/)).

## 2. How a user defines and edits one unit

Warp splits a step into two things and says so in a table ([Configuring factory workers](https://docs.warp.dev/factories/factory-agents/configuration/#choose-what-to-change)):

| Change | Where |
| --- | --- |
| What a stage does and returns | The stage's `prompt`, `inputs`, `outputs` in `factory.yaml` |
| Which stage runs next | `start.to` and the stage's `to` |
| When a person must answer | The stage's `gates` |
| Behavior shared across a worker's stages | The worker file's body |
| A reusable procedure | A skill |
| Model, harness, runner, tools, access | The worker's settings |

"The Blueprint's stage procedures and routing take precedence over workflow instructions in worker prompts" ([same page](https://docs.warp.dev/factories/factory-agents/configuration/)).

**Stage fields** ([Blueprint syntax](https://docs.warp.dev/factories/factory-as-code/#blueprint)):

- `worker` is a reference to a declared worker.
- `inputs` and `outputs` are prose contracts, "not typed schemas".
- `prompt` is inline text, or `{file: path}` relative to the definition root.
- `gates.<name>` takes `prompt`, `outputs` and an optional `when`, all prose.
- `to` is a scalar, or a list of `{stage, when}` with prose `when`.

A stage has no retry, timeout or check field. A stage has no deterministic code, and checks are named in prose or in a skill ("name that skill in the stage prompt", [Stages](https://docs.warp.dev/factories/blueprints/stages/)).

**Worker fields** ([Worker file](https://docs.warp.dev/factories/factory-as-code/#agentsnameagentmd)):

- `description`
- `agentType`: CUSTOM, FOREMAN, TRIAGE, SPEC, IMPLEMENT, REVIEW or VERIFY
- `model` or `harness` (Warp Agent, Claude Code, Codex or Pi, with `auth`)
- `runner`, `environmentId`, `secrets`, `mcpServers`, `workerHost` and `computerUseModel`
- `networkPolicy`, `credentialStrategy` and `idleTimeoutMinutes` (1–60)
- the Markdown body, which holds the shared instructions

All keys are optional and inherit from `agentDefaults`.

**Where the editors live** ([Factory dashboard](https://docs.warp.dev/factories/factory-dashboard/)):

- **Blueprint** page: "the workflow diagram. Select a stage to inspect its worker and instructions, or select a transition to read its condition. Edit the diagram to change the Blueprint, or edit it in the definition files." The docs don't describe the stage editor's individual controls. The claim that it is a form is **unverified**.
- **Workers** page: click a worker to open a **Settings** tab and edit its description, harness, model, runner, host, MCP servers and secrets. These are form fields: the "Harness" dropdown shows an "Auth" field for Claude Code and Codex, and there is a "Model" dropdown. A separate **Prompt** tab holds the free-text instructions, and **Save** sits top right ([Configuring workers](https://docs.warp.dev/factories/factory-agents/configuration/#edit-worker-settings-and-instructions)). New workers are created from the same form, and "Worker names can't be changed after creation" ([Custom workers](https://docs.warp.dev/factories/factory-agents/creating-custom-agents/)).
- **Factory definition** tab: browse and edit the files. "Saving validates the definition and commits all changes together" ([Dashboard](https://docs.warp.dev/factories/factory-dashboard/#edit-definitions-in-the-factory-definition-tab)).
- **Automations** page: an editor for triggers. It "doesn't change execution settings". Per-automation overrides live in the files ([Dashboard](https://docs.warp.dev/factories/factory-dashboard/#manage-workers-and-automations)).
- Outside the app, editors can use a JSON Schema for the definition files (`https://app.warp.dev/api/v1/factory-files/schemas/v1alpha1/...`), a validator script, a `warp/factory-config` PR check, and the Factory MCP tools `get_factory_file_schema` and `validate_factory_files` ([Definitions as code](https://docs.warp.dev/factories/factory-as-code/#validate-a-definition), [Factory MCP](https://docs.warp.dev/factories/factory-mcp/#tool-reference)).

The pattern across these editors: resources (model, harness, runner, MCP, secrets) are form fields, while everything about the workflow (stage prompt, inputs, outputs, transition conditions, gate questions) is free prose.

## 3. Shared or overridable, versions, defaults

- **Hosting decides what is editable.** A Warp-managed definition (the default) is edited in the web app: "Warp validates, commits, and applies changes; its repository stays hidden." A GitHub-hosted definition is read-only in the app and changes by merging to the production branch. A **live-managed** factory is driven through the API and has no files ([Where the definition lives](https://docs.warp.dev/factories/factory-as-code/#where-the-definition-lives), [Dashboard](https://docs.warp.dev/factories/factory-dashboard/#edit-definitions-in-the-factory-definition-tab)).
- **Validation is atomic.** "Warp applies the definition as one unit and keeps the last valid definition if validation fails" ([Validate](https://docs.warp.dev/factories/factory-as-code/#validate-a-definition)).
- **Settings inherit in layers.** `agentDefaults` sets the factory default. A worker overrides it, and a sparse `harness` object overrides it field by field. An automation's **execution overrides** apply to runs from that automation. A `run_agents` dispatch can override environment, runner, host, harness and model for one call, "not the saved worker configuration" ([agentDefaults](https://docs.warp.dev/factories/factory-as-code/#agentdefaults), [Execution overrides](https://docs.warp.dev/factories/factory-as-code/#execution-overrides), [Change settings for one dispatch](https://docs.warp.dev/factories/factory-agents/creating-custom-agents/#change-settings-for-one-dispatch)). A worker-level `secrets` or `mcpServers` list replaces the default list rather than merging with it. An automation that targets a `stage` cannot carry execution overrides and uses the stage worker's settings.
- **There is no per-team override of a stage.** One Blueprint per factory. Warp advises against splitting the same repositories across factories by team, and suggests custom workers and skills instead ([Sizing](https://docs.warp.dev/factories/#sizing-a-factory)).
- **Roles.** Factory user can run work. Editor can edit the definition, workers and automations. Admin can manage roles and delete the factory. There are per-factory overrides of the team role ([Permissions](https://docs.warp.dev/factories/permissions/)).
- **History and undo.** Each save in a Warp-managed definition is a commit, and GitHub-hosted definitions carry git history. The blog says "You can roll back or forward to factory versions, canary them, test them" ([blog](https://warp.dev/blog/open-infrastructure-for-building-a-software-factory)), but the docs describe no history view, diff or revert button for a Warp-managed factory. **Unverified** that one exists. Skills can be pinned to a 40-character commit SHA ([Pin skills](https://docs.warp.dev/factories/factory-as-code/#pin-skills-to-a-commit)).
- **Warp's defaults and your copies.** Setup writes the default workers into your definition, adding a "Selected integration skills" section to their prompts. The examples README says "Warp maintains these resources, and they may change over time" ([00-warp-default-agents](https://github.com/warpdotdev/warp-factory-examples/tree/main/examples/00-warp-default-agents)). The docs say nothing on whether a factory's copy picks up later changes to Warp's defaults (**unverified**). Built-in role and integration skills come "from roles and integrations, not files in your definition" ([Factory skills](https://docs.warp.dev/factories/factory-skills/#skill-sources-in-a-factory)), so they presumably update with Warp (**unverified**).

## 4. Runs in flight when the config changes

Warp pins the Blueprint at run start: "A new run uses the applied Blueprint selected when that run starts. Editing the current definition does not reroute an existing run, and follow-ups to that run retain its Blueprint" ([Blueprints](https://docs.warp.dev/factories/blueprints/#definition-changes-and-work-already-running)). The dashboard repeats this ("Existing work keeps the Blueprint selected when its run started"), and so do troubleshooting and the tuning guide, which says to test a change on fresh work ([Troubleshooting](https://docs.warp.dev/factories/troubleshooting/#a-prompt-change-does-not-change-the-workflow), [Testing and tuning](https://docs.warp.dev/factories/guides/testing-and-tuning-your-factory/)).

The docs only state this for the **Blueprint**. Whether an in-flight work item picks up a changed worker setting (model, prompt) at its next dispatch is **unverified**. A run's details include its "resolved agent configuration" (`agent_config`) ([Agent & run endpoints](https://docs.warp.dev/factories/api-and-sdk/#agent-runs)), so each run records the settings it ran with.

## 5. Failed steps: edit, re-run, resume, display

- Warp documents no "retry this stage" or "edit the failed stage and resume" action. Stopping a run is one click ("**Stop run**… with no confirmation prompt", [Troubleshooting](https://docs.warp.dev/factories/troubleshooting/#you-need-to-stop-a-run)). A blocked third-party-harness run tells you to "include that information in the prompt before starting another run" ([Managing cloud agents](https://docs.warp.dev/platform/managing-cloud-agents/)).
- Recovery happens in conversation. A follow-up "resumes the stage it concerns rather than repeating intake", and revisions continue the same worker conversation on the same PR ([Blueprints](https://docs.warp.dev/factories/blueprints/), [Transitions](https://docs.warp.dev/factories/blueprints/routes/)). A live session can be steered, and any instruction "will interrupt the agent… and resume". A finished session can be continued while its VM lives, or forked to local after ([Session sharing](https://docs.warp.dev/platform/viewing-cloud-agent-runs/)).
- Since the Blueprint is pinned, editing a failed stage's definition cannot help the run that failed. You start new work.
- Platform errors carry a `retryable` flag, and "the platform may automatically retry" ([API errors](https://docs.warp.dev/factories/api-and-sdk/troubleshooting/errors/)). These are infrastructure retries, not a stage setting.
- **Display.** **Runs** groups related worker runs under their parent. A run page shows its timeline, cost and child runs, the worker's full session, and actions to stop, score or turn it into a benchmark task ([Dashboard](https://docs.warp.dev/factories/factory-dashboard/#inspect-runs)). The Oz web app shows children on a **Sub-agents** tab, and "a parent can finish successfully while a child is still running or has failed" ([Managing cloud agents](https://docs.warp.dev/platform/managing-cloud-agents/)). **Home › Waiting on you** lists asked questions, spec reviews, PR reviews, Blocked and Failed items per work item ([Home](https://docs.warp.dev/factories/factory-home/)). The docs don't show a per-stage attempt list (**unverified** that one exists).

## 6. Can the agent or chat change its own config?

Yes, but only as a proposal that a person reviews:

- The Warp Agent's bundled `factory-files` skill "validates before opening a pull request". Factory MCP gives any coding agent the schema and `validate_factory_files` ([Validate with a coding agent](https://docs.warp.dev/factories/factory-as-code/#validate-with-a-coding-agent)).
- "When a worker proposes a change to a definition hosted by Warp, a spec review for the branch appears on Home." There you comment on the diff, then choose **Request changes** or **Approve & merge** ([Dashboard](https://docs.warp.dev/factories/factory-dashboard/#edit-definitions-in-the-factory-definition-tab)). "Anyone on the team, or a worker, can propose changes to the definition" ([How factories work](https://docs.warp.dev/factories/how-factories-work/#how-the-factory-improves-itself)).
- Self-improvement turns repeated Scorer failures into PRs against the definition (prompts, skills, configuration), and "Nothing is adopted without your review". Each PR links its failing runs under **Regressions addressed** ([Self-improvement](https://docs.warp.dev/factories/measure-and-improve/self-improvement/)).
- For standalone cloud agents: "Only human users can create, update, or delete a cloud agent. A request authenticated as a cloud agent itself is rejected" ([Cloud agents](https://docs.warp.dev/platform/agents/#caller-requirements)).

## Mapping to kinby

Sources on the kinby side: `CONTEXT.md`, `src/kinby/factories/shipped/software/factory.yaml`, `apps/web/src/components/library-factory.tsx`, and `FactoryEditCommand` in `src/kinby/contracts/models.py` with `src/kinby/hub/factories.py`.

| Warp | kinby | Note |
| --- | --- | --- |
| Factory | **Factory** | Warp's also binds repositories and compute. kinby's holds behavior only, and the **workbench** holds repo and commands. |
| (none) | **Line** | Warp has no layer between a factory and its runs. Its nearest is an automation's execution overrides. |
| Blueprint | The factory's `steps` list | kinby's order is the list, plus `outcomes` and `failed: {back, max}`. Warp's order is a graph of prose transitions. |
| Stage | **Step** | A Warp stage is always agent work. kinby also has code, command and wait steps. |
| Worker | No equivalent | kinby puts `engine`, `model`, `effort` and `timeout` on each **agent step**. `resume: implement` gives the session continuity a Warp worker's conversation gives. |
| Foreman | The **hub** | kinby routes deterministically from a **step result**. Warp's foreman is an LLM judging prose `when`. |
| Transition `to` / `when` | `outcomes`, `failed: {back, max}` | kinby names outcomes from a **check**. Warp matches prose. |
| Gate | A wait on the user's approval, or an **approval** a turn requests | |
| Stage `inputs` / `outputs` (prose) | **Step result** named values (`results: {title: str}`) and `requires` | kinby's are typed and recorded by a **check**, "never read from an agent's own report". |
| (none) | **Check** / **Hook** | Warp has no deterministic per-stage check. |
| Work item / task | **Work item** | |
| Work item's set of runs | **Factory run** | |
| Run (one agent execution) | **Step attempt** | |
| Environment + runner | **Workbench** | |
| Automation / trigger | **Intake** | |
| Skills (`skills/`, per worker) | Skills of the factory's folder, and the workbench's **picks** | |
| `agentDefaults` → worker → automation → dispatch overrides | Step model → **line**'s default model | kinby has two layers. |
| Warp-managed definition (hidden repo, validate on save, keep last valid) | `factory.edit` plus the **factory check**, where "a broken factory keeps its last good version" | kinby keeps no history. |
| GitHub-hosted definition, read-only in app | **Shipped** factory, read-only, where an edit copies it into the hub | |
| Home › Waiting on you | **Needs human**, approvals, the run page | |
| Scorers, Self-improvement, Benchmarks | No equivalent | |
| Factory roles | No equivalent (single user) | `factory.edit` is `Scope.HUB_ADMIN`. |

### Warp's answer to each "To grill" question

1. **What a step's view holds.** Warp splits it in two. The stage view holds the worker reference, prompt, inputs, outputs, gates and transitions, and all of it is prose. The worker view holds a form (harness, auth, model, runner, host, MCP, secrets) and a Prompt tab of free text. Warp has no retry, check or timeout per stage. For kinby's step: `engine`, `model`, `effort`, `timeout`, `failed.max` and `outcomes` targets are natural form fields. The prompt file and the check or tool code stay text.
2. **Where it lives.** On the factory: the Blueprint diagram, the Workers page and the Factory definition tab. Nothing is editable from a run. A run page offers stop, score and benchmark, and recovery is a conversational follow-up.
3. **Whose factory changes.** One definition per factory. Per-use overrides cover execution settings only (automation overrides, per-dispatch `run_agents`), never a stage's procedure. In kinby terms, a line could override engine and model but not a step's prompt or routing.
4. **Runs in flight.** They keep the Blueprint they started with, including on follow-ups. Whether worker settings are pinned too is **unverified**. kinby today does the opposite for tools: "a step runs the factory's tools from the factory's own folder, as they are then" (`CONTEXT.md`, Run folder).
5. **Edits to a shipped factory.** Warp writes its defaults into your definition at setup and says they "may change over time". It documents no merge-forward or "differs from default" view (**unverified**). Warp has not solved this problem.
6. **Changing the shape.** Yes. The diagram editor ("Edit the diagram to change the Blueprint") and the files allow adding stages, groups and transitions. Stage declaration order doesn't matter, and transition order does.
7. **Undo.** Every Warp-managed save is a commit, and GitHub mode has git. The blog promises roll back and forward, but the docs show no history or revert UI (**unverified**).
8. **The owner from chat.** Yes, through the same validation, as a proposal: the agent or worker writes a branch, a spec review appears on Home, and the person chooses Approve & merge or Request changes. Self-improvement takes the same path.

## Ideas worth borrowing for #1384

1. **Split "what the step does" from "what runs it".** Make engine, model, effort and timeout form fields that inherit (factory default → step → line override), and keep the prompt and routing as text on the step. A line can then override how a step runs without forking the factory.
2. **Pin the factory version at run start, and make moving to a newer version explicit.** Warp pins, which keeps runs reproducible. But then editing the step that just failed cannot help the run that failed. kinby can do better: a retry or send-back from needs human could offer "use the edited factory" as a choice, with the run recording the factory hash it uses (`FactoryResult.hash` already exists).
3. **Make every save a commit.** One validated, atomic save (already `factory.edit` plus the factory check) that also commits to a hub-side git history would give undo, diff against the shipped version, and "differs from shipped" almost for free.
4. **Use a diagram as the entry point.** Clicking a step opens its editor, and clicking an arrow shows what sends the run there (`outcomes`, `failed.back`). kinby's routes are deterministic, so the diagram can show them exactly, where Warp's are prose.
5. **Treat a chat edit as a proposal.** The owner's edit from chat becomes a reviewable diff (Approve / Request changes) through the same `factory.edit` and check. That is the same path as a user edit, and it is how Warp treats agent-written definition changes.
