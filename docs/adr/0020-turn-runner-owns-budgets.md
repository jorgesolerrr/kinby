# The turn runner owns budgets

The turn runner computes effective budgets during turn preparation. `TurnContext` carries the budgets outside the turn request and checkpointed graph state.

LangGraph's recursion limit enforces steps. The model node checks cumulative tokens after each call. `asyncio.timeout` bounds the graph invocation. A crossed limit fails the turn, while the next turn starts from the last completed checkpoint.

An approval keeps the turn open. The graph checkpoint metadata records the original limits, the starting graph step, the resume count, and active runtime. A resumed turn receives only its remaining steps and seconds. Time spent waiting for the user's answer does not consume the seconds budget.

The daily cost check runs before `turn.started` because it limits the instance rather than work inside one turn.
