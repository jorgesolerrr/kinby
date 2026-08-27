# Model turns use LangGraph and LangChain

The turn runner uses a one-node LangGraph workflow because later turns must park and resume from checkpointed working state. LangChain resolves `models.main` without provider-specific loop code and includes the OpenAI and Anthropic integrations used by the shipped examples. An in-memory checkpointer keeps working state for one session, while the event log remains the durable transcript store.
