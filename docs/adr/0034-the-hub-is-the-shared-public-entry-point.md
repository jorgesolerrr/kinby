# The hub is the shared public entry point

Caddy routes public traffic to the hub, which forwards instance requests over a private container network. The browser connects to the hub. This keeps public routing in one place as instances are created and removed. Adoption preserves the coder's existing webhook URL.

Instances keep running if the hub stops, although requests routed through the hub cannot reach them during that outage. The hub must not own their process lifetime merely because it proxies their requests.

For hub-managed instances, this changes the direct Caddy-to-coder route in [The coder runs on a public box behind Caddy](0030-the-coder-runs-on-a-public-box-behind-caddy.md). Authentication and transport details belong to [Server slice: the contract over WebSocket and the hub's routes](https://github.com/jorgesolerrr/kinby/issues/217).

Decision agreed during [Hub and instance lifecycle](https://github.com/jorgesolerrr/kinby/issues/215).
