# A cookie session for the browser and a control token per instance

The hub has one access token. `kinby hub` generates it on first start, prints it once, and stores only its hash in the hub directory. `kinby hub token rotate` replaces it. An authenticated connection holds every scope, because a hub has one user.

A browser cannot set headers on a WebSocket upgrade. The web app posts the token to `POST /auth/login` and receives an `HttpOnly; SameSite=Strict` session cookie, marked `Secure` when the request is HTTPS (including when Caddy forwards `X-Forwarded-Proto: https`). The default listen is plain HTTP, so a cookie that is always `Secure` would never return on that transport. The upgrade carries the cookie and the server checks the Origin host. Sessions live in the hub's SQLite, so a hub restart does not log the user out. Other clients send `Authorization: Bearer <token>` on the upgrade. We rejected a token in the first frame, which keeps it in page JavaScript, and a token in the query string, which puts it in access logs.

The hub reaches each instance with that instance's own control token. The hub generates it at creation, stores it in the instance's protected environment file, and presents it as a bearer token on every upstream connection. The instance's `/ws` grants the thread and instance scopes. Drain, force-stop, and the compatibility probe require a new scope, `instance:lifecycle`, which only the private `GET /control` route grants. The hub's public relay never forwards to `/control`. The access token never enters an instance, so an agent cannot call hub methods.

An agent can read its own environment, so it could drain its own instance. We accept that. The agent can already kill its own process, and the line that matters is that agents never hold hub scopes. Mutual TLS on the private network would close the gap at a cost one user does not justify.

An instance's `GET /health` reports `contract_version` and a `capabilities` list such as `ws` and `drain`. The hub reads it before each lifecycle operation and reports an incompatible lifecycle endpoint when `drain` is missing. It never describes an interrupting stop as graceful. A capabilities list answers "can you drain?" directly, where a version comparison would make the hub keep a table of what each version supports.

The contract server logs a frame's type, ID, and method, and never its parameters. One blanket rule cannot drift the way a per-method redaction list would. Secret setters return names only, and reads report whether each secret is set.

Subscription login runs as a lifecycle operation in the temporary setup container. Its running step carries a URL and a code for the human to complete elsewhere. The hub relays no terminal to the browser, so a package whose login has no URL-and-code mode cannot be set up from the app in this version.

Decision agreed during [Server slice: the contract over WebSocket and the hub's routes](https://github.com/jorgesolerrr/kinby/issues/217).
