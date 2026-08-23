# Research: eval harness options for a LangGraph agent (wayfinder #2)

Date: 2026-08-22. Method: official docs and GitHub sources only. Every claim carries a URL.

## Question

Which eval tooling fits kinby: a LangGraph, model-agnostic (`init_chat_model("provider:model")`) personal-agent harness, each instance in its own directory/container? Needs:

1. cross-model comparison of the same harness
2. a token-cost metric next to correctness
3. runnable inside a container
4. ranking prompt/behavior-file variants
5. Python-native
6. multi-turn agentic tasks (coding with tools, memory recall)

## Short answer

- **Harness/task evals: Inspect AI**, with kinby's LangGraph graph plugged in through the in-process `agent_bridge()` (model name `inspect`). Inspect gives multi-model runs from one command, Docker sandboxes per sample, per-model token usage in every log, and per-sample token/message/time limits. Variants of a behavior file become task parameters.
- **Memory-feed bake-off (files vs RAG vs graph): also Inspect**, same task set, memory backend as a task parameter, with the retrieval-quality half scored by Ragas metrics inside an Inspect scorer (`convert_to_ragas_messages` takes LangChain messages directly).
- Keep a thin **pytest + LangGraph** layer for fast, deterministic regression checks, using `UsageMetadataCallbackHandler` for tokens. Not as the main harness.
- LangSmith is the best-integrated option for LangGraph but the full platform is hosted; self-hosting is an Enterprise add-on. promptfoo is Node-based. DeepEval and Ragas are metric libraries rather than agent runners.

## Comparison table

| Tool | Multi-turn agent | Sandbox / Docker | Token usage & cost | Model-agnostic runner | Self-hosted vs hosted | Maturity |
|---|---|---|---|---|---|---|
| Inspect AI | Built-in ReAct/Deep agents, `generate_loop()`, agent bridge for external frameworks | Docker is the primary sandbox, plus k8s/Modal/etc.; per-sample `compose.yaml` | `ModelUsage` per model in every log (input/output/cache/reasoning tokens); `token_limit` per sample; no built-in $ pricing | Yes: `provider/model`, pass a list of models to `eval()` | Fully local, MIT | UK AISI, 2.6k stars, active |
| LangSmith | Trajectory evals, `agentevals`/`openevals`, multi-turn simulation | None (runs your code wherever you run it) | Automatic cost from `usage_metadata` + model pricing map | Yes: target is any Python callable | Hosted SaaS; self-host is an Enterprise add-on | Mature commercial product |
| promptfoo | `simulated-user` provider, `_conversation` var, Python provider | Self-host via Docker image (web UI); no sample sandbox | Provider returns `tokenUsage` and `cost`; `cost` assertion | Yes via custom Python provider | Local CLI, MIT; Enterprise for scale | 24.5k stars, Node/TS, now part of OpenAI |
| DeepEval | `ConversationalTestCase`/`Turn` with `tools_called`; LangGraph `CallbackHandler` | None | `token_cost` field on `LLMTestCase` (you supply the number) | Judge: any LLM via `DeepEvalBaseLLM`; runner: pytest | Runs locally; Confident AI cloud optional | Apache-2.0, 17.8k stars |
| Ragas | Agent metrics on message lists (`ToolCallAccuracy`, `AgentGoalAccuracy`) | None | `TokenUsageParser` + `Result.total_cost()` (judge cost only by default) | Metrics only; bring your own runner | Local library | Apache-2.0, 15.4k stars |
| LangGraph + pytest | Whatever you write | Whatever you write | `AIMessage.usage_metadata`, `get_usage_metadata_callback()` | Yes (`init_chat_model`) | Local | You maintain it |

## Per-tool notes

### Inspect AI (UK AI Security Institute)

- Multi-turn agents: Inspect ships a ReAct agent and "Deep Agent for long-horizon tasks with subagent delegation, memory, and planning"; tool loops run via `generate_loop()`. <https://inspect.aisi.org.uk/agents.html>
- External frameworks: the Agent Bridge "supports popular frameworks including OpenAI Agents SDK, Pydantic AI, and LangChain" and fully custom agents. Agents set `model="inspect"` and "their native model calling functions are routed through the current Inspect model provider". Two modes: in-process `agent_bridge()` and `sandbox_agent_bridge()` for agents running inside a container (proxy on localhost:13131). A LangChain agent uses `ChatOpenAI(model="inspect/google/gemini-1.5-pro")` to route through Inspect. <https://inspect.aisi.org.uk/agent-bridge.html>. The docs show LangChain, not LangGraph, but the mechanism is an OpenAI-compatible endpoint, so `init_chat_model("openai:inspect")` pointed at the bridge is the expected integration path. Untested here.
- Sandboxing: "Docker is the built-in primary sandbox environment"; Kubernetes, Daytona, Modal, AWS EC2, Proxmox, Vagrant exist as extensions. Uses standard `compose.yaml`; auto-generated config sets `network_mode: none`. Each sample can define its own sandbox, files and setup script; samples do not share sandboxes. Only work requested via `sandbox()` runs in the container. <https://inspect.aisi.org.uk/sandboxing.html>
- Token usage: `EvalStats` carries `model_usage` and `role_usage` (dict of `ModelUsage`). <https://inspect.aisi.org.uk/reference/inspect_ai.log.html>. `ModelUsage` fields: `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_cache_write`, `input_tokens_cache_read`, `reasoning_tokens`. <https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/model/_model_output.py>. No dollar pricing table; cost is tokens times a price map you supply.
- Limits: token, message, time and working-time limits, stackable; `LimitExceededError` when exceeded. <https://inspect.aisi.org.uk/reference/inspect_ai.util.html>
- Model-agnostic: `provider/model-name` naming; providers include OpenAI, Anthropic, Google, Mistral, DeepSeek, Bedrock, Azure, Groq, Together, Ollama, vLLM, HF. Pass a comma-separated `--model` list or a Python list to `eval()` to run the same task on several models; `--model-spec` for per-model generation configs; eval sets for larger grids. <https://inspect.aisi.org.uk/models.html>
- Hosting/maturity: MIT, 2.6k stars, maintained by UK AISI, runs entirely locally. <https://github.com/UKGovernmentBEIS/inspect_ai>

### LangSmith evaluations

- Concepts: datasets of examples, evaluators (code, LLM-judge, pairwise), experiments that can be compared against each other. <https://docs.langchain.com/langsmith/evaluation-concepts>
- SDK: `evaluate()` / `aevaluate()` take a target callable, `data`, `evaluators`, `experiment_prefix`, `max_concurrency`; reserved metadata keys for `models`, `prompts`, `tools`. <https://docs.langchain.com/langsmith/evaluate-llm-application>
- Agent evals: final response, single step, and trajectory evaluation, capturing LangGraph trajectories with `astream(subgraphs=True, stream_mode="debug")`. <https://docs.langchain.com/langsmith/evaluate-complex-agent>. `agentevals` provides strict/unordered/subset/superset trajectory match, LLM-as-judge trajectory, and LangGraph graph-trajectory evaluators; MIT; LangSmith integration optional. <https://github.com/langchain-ai/agentevals>. `openevals` `run_multiturn_simulation` + `create_llm_simulated_user` drive a multi-turn conversation and evaluators score the full message list. <https://docs.langchain.com/langsmith/multi-turn-simulation>
- Cost: set `usage_metadata` on the run; with `ls_provider` + `ls_model_name` and a model pricing map, "LangSmith will automatically calculate and aggregate the token-based costs for traces". <https://docs.langchain.com/langsmith/cost-tracking>
- Sandbox: none; the target runs in your process.
- Hosting: "Self-hosted LangSmith is an add-on to the Enterprise plan"; needs PostgreSQL, Redis, ClickHouse. <https://docs.langchain.com/langsmith/self-hosted>
- Fit: strongest LangGraph integration, but results live on LangChain's servers unless you pay Enterprise. `agentevals`/`openevals` are importable without LangSmith and are worth reusing as scorers.

### promptfoo

- Runner: Node.js CLI (`npm install -g promptfoo`, also brew/pip), TypeScript/JS, MIT, 24.5k stars; README states "Promptfoo is now part of OpenAI". <https://github.com/promptfoo/promptfoo>
- Python provider: implement `call_api(prompt, options, context)`, return `output`, optional `tokenUsage` (`total`, `prompt`, `completion`), `cost`, `latencyMs`; `conversationEnded` flag for multi-turn. <https://www.promptfoo.dev/docs/providers/python/>
- Multi-turn: `promptfoo:simulated-user` provider with `maxTurns`, `instructions`, `initialMessages`; stops on `###STOP###`. <https://www.promptfoo.dev/docs/providers/simulated-user/>. `_conversation` variable and `storeOutputAs` for chained tests. <https://www.promptfoo.dev/docs/configuration/reference/>
- Cost: `cost` assertion "requires LLM providers to return cost information. Currently this is only supported by OpenAI GPT models and custom providers." <https://www.promptfoo.dev/docs/configuration/expected-outputs/deterministic/>
- Hosting: Express server in Docker/Helm; SQLite at `~/.promptfoo/`; self-hosted build has no RBAC or horizontal scaling. <https://www.promptfoo.dev/docs/usage/self-hosting/>
- Fit: good side-by-side prompt/model matrix UI, but the agent is wrapped as a black-box Python provider and the core is Node. No per-sample sandbox.

### DeepEval

- "Similar to Pytest but specialized for unit testing LLM apps"; Apache-2.0, 17.8k stars, `pip install -U deepeval`. <https://github.com/confident-ai/deepeval>
- `LLMTestCase` has nine params including `tools_called`, `expected_tools`, `token_cost`, `completion_time`. <https://deepeval.com/docs/evaluation-test-cases>
- Multi-turn: `ConversationalTestCase` of `Turn`s (`role`, `content`, optional `tools_called`), plus a conversation simulator from `ConversationalGolden`. <https://deepeval.com/docs/evaluation-multiturn-test-cases>
- LangGraph: `CallbackHandler` from `deepeval.integrations.langchain` passed in graph config; every node/model/tool call becomes a span; trajectory evaluation across spans. <https://deepeval.com/integrations/frameworks/langgraph>
- Runner: `assert_test` with `deepeval test run`, or `evaluate()`; "deepeval runs entirely locally", Confident AI cloud optional; any judge via `DeepEvalBaseLLM`. <https://deepeval.com/docs/evaluation-introduction>
- Fit: Python, local, pytest-shaped, LangGraph callback. Weak on cross-model matrices and sandboxes; `token_cost` is a number you compute yourself.

### Ragas

- Apache-2.0, 15.4k stars, `pip install ragas`. <https://github.com/explodinggradients/ragas>
- Agent metrics: `ToolCallAccuracy` (strict/flexible order), tool-call F1, `AgentGoalAccuracy` with or without reference; inputs are `HumanMessage`/`AIMessage`/`ToolMessage` lists and `ToolCall`s. <https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>
- LangGraph: `convert_to_ragas_messages` from `ragas.integrations.langgraph` converts LangChain messages (system messages skipped). <https://docs.ragas.io/en/stable/howtos/integrations/_langgraph_agent_evaluation/>
- Cost: "By default, Ragas does not calculate token usage for evaluate()"; supply a `TokenUsageParser` (e.g. `get_token_usage_for_openai`) and call `Result.total_cost()`. This measures the judge's cost, not the agent's. <https://docs.ragas.io/en/stable/howtos/applications/_cost/>
- Fit: metric library, retrieval-focused. Useful inside the memory bake-off; not a harness.

### Roll-your-own: LangGraph + pytest

- Tokens: `AIMessage.usage_metadata` (`input_tokens`, `output_tokens`, `total_tokens`, cache details); `UsageMetadataCallbackHandler` or `get_usage_metadata_callback()` aggregate by model name. `init_chat_model("provider:model")` is the documented unified constructor. <https://docs.langchain.com/oss/python/langchain/models>
- Everything else (dataset format, multi-model loop, Docker per sample, log format, viewer, limits, retries) you write and maintain. Inspect already provides each of these.

## Recommendation and rationale

**Harness/task evals: Inspect AI.**

- Requirement 1 (cross-model): one `eval(task, model=[...])` call; `--model-spec` for per-model settings. Kinby's graph gets its model via the bridge, so the harness code does not change per provider.
- Requirement 2 (token cost): `ModelUsage` per model per sample is in the log by default; multiply by a price map in a scorer or post-process. `token_limit` also caps runaway runs.
- Requirement 3 (container): Docker sandbox per sample; kinby's per-instance directory maps to a per-sample `compose.yaml` with files copied in. The sandbox bridge allows running kinby entirely inside the container if desired.
- Requirement 4 (variant ranking): expose the behavior-file path as a task parameter and run an eval set over the grid; compare in the log viewer.
- Requirement 5: Python, MIT, local.
- Requirement 6: Inspect is built for multi-turn tool-using agents with scorers over full transcripts.

Risks: the bridge is documented for LangChain, not LangGraph specifically, so a spike is needed (half a day) to confirm `init_chat_model` routes through `inspect/...` cleanly, including tool calls. Inspect has no dollar pricing table; a small price map is needed.

**Memory-feed bake-off: Inspect AI, with Ragas scorers.** Same tasks, `memory_backend` as a task parameter (files / RAG / graph). Correctness from Inspect scorers; retrieval quality via Ragas `ToolCallAccuracy`/`AgentGoalAccuracy` on the converted message list; cost from `ModelUsage`. One log format for both studies.

**Runner-up:** LangSmith + `agentevals` if hosted traces are acceptable. Reuse `agentevals` trajectory matchers as Inspect scorers regardless.

## Open questions

- Verify `sandbox_agent_bridge()` with a kinby container image (network off by default; the bridge proxy must be reachable).
- Decide the price map source (per-provider JSON in repo) and where cost is recorded (scorer metadata vs post-processing).
