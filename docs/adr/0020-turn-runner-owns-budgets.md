# The turn runner owns budgets

The turn runner computes effective budgets during turn preparation. `TurnContext` carries the budgets outside the turn request and checkpointed graph state.

LangGraph's recursion limit enforces steps. The model node checks cumulative tokens after each call. `asyncio.timeout` bounds the graph invocation. A crossed limit fails the turn, while the next turn starts from the last completed checkpoint.

The daily cost check runs before `turn.started` because it limits the instance rather than work inside one turn.
