# The hub is the shared public entry point

Caddy routes public traffic to the hub, which forwards instance requests over a private container network. The browser connects to the hub. This keeps public routing in one place as instances are created and removed. Adoption preserves the coder's existing webhook URL.

Instances keep running if the hub stops, although requests routed through the hub cannot reach them during that outage. The hub must not own their process lifetime merely because it proxies their requests.

For hub-managed instances, this changes the direct Caddy-to-coder route in [The coder runs on a public box behind Caddy](0030-the-coder-runs-on-a-public-box-behind-caddy.md). Transport details are in [The contract crosses the network in typed frames, one socket per instance](0048-the-contract-crosses-the-network-in-typed-frames-one-socket-per-instance.md), and authentication in [A cookie session for the browser and a control token per instance](0049-a-cookie-session-for-the-browser-and-a-control-token-per-instance.md).

Decision agreed during [Hub and instance lifecycle](https://github.com/jorgesolerrr/kinby/issues/215).
