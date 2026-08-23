# Hot-plugging native Python tools into a running LangGraph agent

Wayfinder ticket: jorgesolerrr/kinby #3. Date: 2026-08-22.
Question: what is the reliable way to add a Python tool to a running LangGraph agent with no restart?

## Short answer

1. Do not use `importlib.reload`. Load each `tools/*.py` as a fresh module under a unique name with `spec_from_file_location` + `module_from_spec` + `exec_module`, collect the `BaseTool` objects it exposes, and swap the whole tool list atomically in a registry. LangChain's `@tool` decorator has no global registry, so re-executing a file just produces new `StructuredTool` objects; nothing needs un-registering.
2. Detect changes with `watchfiles` (Rust `notify`, debounced) and fall back to mtime polling (`force_polling=True` / `WATCHFILES_FORCE_POLLING`) on network or Docker mounts.
3. Offer `importlib.metadata.entry_points(group="kinby.tools")` as the packaged route; it is the same mechanism pytest uses (`pytest11` group) and pluggy wraps (`load_setuptools_entrypoints`).
4. In LangGraph, call `llm.bind_tools(current_tools)` inside the model node on every call (it returns a new Runnable; it is cheap and does not mutate the model) and build the tool-executing node from the same snapshot. Prebuilt `ToolNode` fixes its tool set at construction, so either rebuild it per call or write a small custom tool node that looks tools up in the registry. `create_agent` middleware can only filter tools registered up front, not add new ones.
5. Caching: on Anthropic, any change to tool definitions invalidates the whole prefix (tools sit first in the `tools -> system -> messages` hierarchy). On OpenAI, tool definitions and their ordering are part of the prefix as well. There is no ordering trick that avoids this; the mitigation is to change the tool list rarely (batch/debounce), keep tool order stable (sort by name), and accept one cache write (1.25x input price on Anthropic) per tool-list change.

## 1. Directory-scan loading of `tools/*.py`

### Loading a file by path

The Python docs give a recipe, "Importing a source file directly":

```python
spec = importlib.util.spec_from_file_location(module_name, file_path)
module = importlib.util.module_from_spec(spec)
sys.modules[module_name] = module
spec.loader.exec_module(module)
```

with the caveat that it "is an approximation of an import statement where the file path is specified directly, rather than `sys.path` being searched" (https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly).

`importlib.import_module` is the alternative when `tools/` is a package on `sys.path`, but: "If you are dynamically importing a module that was created since the interpreter began execution (e.g., created a Python source file), you may need to call `invalidate_caches()` in order for the new module to be noticed by the import system" (https://docs.python.org/3/library/importlib.html#importlib.import_module, https://docs.python.org/3/library/importlib.html#importlib.invalidate_caches). Path-based loading sidesteps the finder cache entirely, which is one reason to prefer it for a hot-plug directory.

### Why `importlib.reload` is the wrong primitive

The reload docs list the pitfalls verbatim (https://docs.python.org/3/library/importlib.html#importlib.reload):

- "When a module is reloaded, its dictionary (containing the module's global variables) is retained. ... If the new version of a module does not define a name that was defined by the old version, the old definition remains." So a deleted tool function survives a reload.
- "Other references to the old objects (such as names external to the module) are not rebound to refer to the new objects". A `ToolNode` or bound model holding the old `StructuredTool` keeps the old code.
- "If a module imports objects from another module using `from ... import ...`, calling `reload()` for the other module does not redefine the objects imported from it".
- "reloading the module that defines the class does not affect the method definitions of the instances".
- "extension modules are not designed to be initialized more than once, and may fail in arbitrary ways when reloaded".

Decorator re-registration: LangChain's `@tool` is a factory that returns `StructuredTool.from_function(...)` (https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/tools/convert.py); there is no module-level registry that a second execution would duplicate. The stale-reference problem is therefore entirely on kinby's side: whatever holds the tool objects must be replaced, not patched. The reliable pattern is "load fresh, then swap":

```python
def load_tool_file(path: Path) -> list[BaseTool]:
    name = f"kinby_tools.{path.stem}_{path.stat().st_mtime_ns}"  # unique per version
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # raises -> keep previous version
    return [v for v in vars(mod).values() if isinstance(v, BaseTool)]
```

Notes: use a unique module name so old versions are not reused from `sys.modules`; old objects are garbage collected once nothing references them ("old objects are only reclaimed after their reference counts drop to zero"), so also drop the old entry from `sys.modules`. Wrap `exec_module` in try/except so a syntax error in a tool file leaves the previous tool set in place. Tool files should avoid `from tools.other import x` cross-imports, or that other file must be loaded first; keep each tool file self-contained.

### watchfiles vs mtime polling

`watchfiles`: "Underlying file system notifications are handled by the Notify rust library" and "Debouncing changes - e.g. grouping changes into batches rather than firing a yield/reload for each file changed is managed in rust" (https://watchfiles.helpmanual.io/). `watch()` / `awatch()` signature: `debounce: int = 1600` ms, `step: int = 50` ms, `force_polling: bool | None = None`, `poll_delay_ms: int = 300`, `recursive: bool = True`, `stop_event` (https://watchfiles.helpmanual.io/api/watch/). `force_polling` is "Determined by `WATCHFILES_FORCE_POLLING` environment variable or WSL detection"; "if the value is `false`, `disable` or `disabled`, force polling is disabled; otherwise, force polling is enabled". So watchfiles already contains the mtime-polling fallback; a hand-rolled `os.stat().st_mtime_ns` loop is only worth it to avoid the dependency. For kinby: `awatch("tools/", debounce=1600)` in an asyncio task alongside the agent, with `WATCHFILES_FORCE_POLLING=true` documented for Docker bind mounts.

Editor save patterns (write temp + rename) produce several events per save; watchfiles' debounce collapses them, and the loader should treat any event in the directory as "rescan everything" rather than per-file deltas, which also handles deletes correctly.

## 2. Entry points as the packaged route

`importlib.metadata.entry_points(group=...)`: "Returns a `EntryPoints` instance describing entry points for the current environment. Any given keyword parameters are passed to the `select()` method" (https://docs.python.org/3/library/importlib.metadata.html#entry-points). The selectable API (`group=` keyword) arrived in Python 3.10 / `importlib_metadata` 3.6; since Python 3.12 / `importlib_metadata` 5.0 it "always returns an `EntryPoints` object". kinby requires `>=3.10`, so `entry_points(group=...)` works everywhere it runs. Each `EntryPoint` has `.load()` which imports `.module` and returns `.attr`.

pytest: "pytest looks up the `pytest11` entrypoint to discover its plugins, thus you can make your plugin available by defining it in your `pyproject.toml` file" with `[project.entry-points.pytest11] myproject = "myproject.pluginmodule"` (https://docs.pytest.org/en/stable/how-to/writing_plugins.html#making-your-plugin-installable-by-others). pluggy wraps the same call as `PluginManager.load_setuptools_entrypoints(group)` and exposes `register()` / `unregister()` / `is_registered()` for dynamic lifecycle (https://pluggy.readthedocs.io/en/stable/index.html#loading-setuptools-entry-points, https://pluggy.readthedocs.io/en/stable/api_reference.html#pluggy.PluginManager.unregister).

For kinby: a `kinby.tools` group where each value is `pkg.module:tool_or_list`. Entry points are read from installed distribution metadata, so they are "hot" to the extent that a `pip install` of a new package into the running environment is followed by a rescan (`entry_points()` re-reads `sys.path` on each call; call `importlib.invalidate_caches()` first). This is the route for third-party tool packages; the directory scan is the route for the user's own scripts.

## 3. LangGraph / LangChain binding

`BaseChatModel.bind_tools(tools, *, tool_choice=None, **kwargs) -> Runnable[LanguageModelInput, AIMessage]` returns a new `Runnable` binding (`self.bind(tools=...)`); it does not mutate the model (https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/chat_models.py). Calling it inside the node per invocation is therefore safe and cheap (it formats tool schemas to the provider dict format each call; no network).

`create_react_agent` binds once at build time: `model = model.bind_tools(tool_classes + llm_builtin_tools)` before `call_model` is defined, and builds `ToolNode([...])` from the same list (https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py). The hand-written ReAct example in the docs also does `llm_with_tools = llm.bind_tools(tools)` at module level (https://docs.langchain.com/oss/python/langgraph/workflows-agents). Nothing in LangGraph requires this; a node is a plain function, so the binding can be moved inside:

```python
def call_model(state, runtime):
    tools = registry.snapshot()  # immutable list, sorted by name
    return {"messages": [llm.bind_tools(tools).invoke(state["messages"])]}
```

`ToolNode` (https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py): `__init__(self, tools: Sequence[BaseTool | Callable], *, name="tools", ...)` fills a private `self._tools_by_name` and, per tool, `self._injected_args[tool_.name]` "once during initialization in a single pass"; `tools_by_name` is a read-only property. Lookup at call time is `self.tools_by_name.get(call["name"])`, and an unknown name yields a `ToolMessage` with `"Error: {requested_tool} is not a valid tool, try one of [{available_tools}]."`. The injected-args cache means mutating the private dict is not enough; a `ToolNode` must be rebuilt to add a tool. The source mentions "dynamically registered tools that are not in self.tools_by_name (e.g., tools added via middleware's wrap_tool_call)", but that is the `create_agent` middleware path. Options for kinby:

- Rebuild `ToolNode(registry.snapshot())` inside a wrapper node each call (construction is cheap; it only introspects signatures).
- Or write a ~20-line custom tool node that does `registry.get(call["name"]).invoke(call["args"])` and returns the same "not a valid tool" error for unknown names (this is what the docs' Graph API example does with `tools_by_name[tool_call["name"]]`).

The graph structure (nodes, edges, conditional edge "has tool calls?") is fixed at `compile()`, but the tool set is data, so no recompile is needed.

`create_agent` (LangChain 1.x) middleware: `wrap_model_call` can `request.override(tools=subset)`, but the docs say tools must be "known at agent creation time" and "All available tools need to be registered upfront" (https://docs.langchain.com/oss/python/langchain/tools, https://docs.langchain.com/oss/python/langchain/middleware/custom). So `create_agent` supports dynamic *filtering*, not dynamic *addition*. kinby's agent is a hand-built graph (`agent/agent.py`), so the per-node binding above is the right fit.

Consistency rule: take one registry snapshot per turn (or at least per model call + its tool execution) so the tool the model chose is the one executed; an unknown-tool error message still lets the model recover if a tool was removed mid-turn.

## 4. Prompt-caching impact

### Anthropic

"Cache prefixes are created in the following order: `tools`, `system`, then `messages`. This order forms a hierarchy where each level builds upon the previous ones." and "Changes at each level invalidate that level and all subsequent levels." The invalidation table: "Tool definitions ... Modifying tool definitions (names, descriptions, parameters) invalidates the entire cache" (https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching). Because the API fixes the order as tools first, there is no "system first" ordering trick; tools are always the root of the prefix.

Cost of a miss: a fresh cache write costs 1.25x base input (5-min TTL) or 2x (1-hour); reads cost 0.1x; the default TTL is 5 minutes. Minimum cacheable length is model dependent (512 to 4,096 tokens). Up to 4 breakpoints; automatic lookback is 20 blocks. So one tool-list change costs one full re-write of tools + system + the conversation so far; the following turn is cached again. By contrast, `tool_choice` changes leave the tools and system caches intact.

Mitigations: (a) emit a stable, name-sorted tool list so a rescan with no real change produces byte-identical JSON; (b) debounce file events (watchfiles default 1.6 s) and apply the new tool set at a turn boundary, not mid-turn; (c) keep tool descriptions/schemas deterministic (no timestamps, no dict-ordering differences from pydantic models); (d) accept the miss: a 10k-token prefix re-write is one 1.25x charge, not a recurring cost.

### OpenAI

"Tool definitions, descriptions, parameter schemas, and tool ordering can contribute to the prefix" and "Changes to tool descriptions, parameter schemas, schema keys, or ordering can reduce cache reuse" (https://developers.openai.com/api/docs/guides/prompt-caching). Caching is automatic, requires exact prefix matches, minimum 1,024 tokens (model dependent, up to 2,048), hits in 128-token increments, cache stays warm "5 to 10 minutes of inactivity, up to a maximum of one hour". Advice: "static content like instructions and examples at the beginning of your prompt, and put variable content, such as user-specific information, at the end". Use `prompt_cache_key` so all kinby requests for a thread route to the same cache. The same mitigations apply; on OpenAI there is no explicit breakpoint, so only the longest matching prefix (in 128-token steps) survives a tool change. The docs do not state where tools sit relative to messages in the hashed prefix, so treat tools as prefix content that invalidates the cache whenever it changes.

## Recommended design for kinby

1. `agent/tools/registry.py`: `ToolRegistry` holding an immutable, name-sorted `tuple[BaseTool, ...]`; `reload()` rescans `tools/*.py` (path loader above) and `entry_points(group="kinby.tools")`, validates, and swaps atomically under a lock; `snapshot()` returns the tuple.
2. A background `awatch("tools/")` task that calls `registry.reload()` (debounced) and logs which tools were added/removed/kept.
3. Model node: `llm.bind_tools(registry.snapshot())` per call. Tool node: custom lookup against the same snapshot stored in state for the turn (or rebuild `ToolNode`).
4. Treat a tool-list change as a one-time cache miss; keep ordering stable to avoid spurious misses.
5. Optional `/reload` user command that forces a rescan, for filesystems without notifications.

## Sources

- https://docs.python.org/3/library/importlib.html (reload caveats, import_module, invalidate_caches, file-path recipe)
- https://docs.python.org/3/library/importlib.metadata.html (entry_points selectable API)
- https://docs.pytest.org/en/stable/how-to/writing_plugins.html (pytest11 group)
- https://pluggy.readthedocs.io/en/stable/index.html (load_setuptools_entrypoints, register/unregister)
- https://watchfiles.helpmanual.io/ and https://watchfiles.helpmanual.io/api/watch/
- https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/chat_models.py (bind_tools)
- https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/tools/convert.py (@tool)
- https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py (ToolNode)
- https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py (create_react_agent)
- https://docs.langchain.com/oss/python/langgraph/workflows-agents
- https://docs.langchain.com/oss/python/langchain/tools and https://docs.langchain.com/oss/python/langchain/middleware/custom
- https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
- https://developers.openai.com/api/docs/guides/prompt-caching
