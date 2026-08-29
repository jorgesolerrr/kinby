# Model turns checkpoint only after graph exit

LangGraph persists a turn's working state only when the graph exits. With step checkpoints, the model node can persist a tool call before the tools node persists its result. An interrupt during the tool leaves that unmatched call in thread history, and providers reject the next turn. Exit-only persistence keeps interrupted and failed turns out of checkpoint history while complete turns still persist.
