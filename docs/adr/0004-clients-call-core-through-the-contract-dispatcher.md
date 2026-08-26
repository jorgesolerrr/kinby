# Clients call core through the contract dispatcher

Every client call uses Pydantic command and result models from `kinby.contracts` and crosses the name-routed dispatcher. The dispatcher checks the caller's scope, validates the payload, invokes the handler, and translates failures into the shared error envelope. Clients do not call core handlers directly.

Pydantic is a runtime dependency because the same models define and validate this boundary. The in-process CLI uses a thin contract client, holds all four initial scopes, and owns the `asyncio.run` call.

## Consequences

- Authorization happens before payload validation or handler execution.
- Clients depend on contracts, while core owns routing and handlers.
- A future server can use the same dispatcher without changing command or result shapes.
