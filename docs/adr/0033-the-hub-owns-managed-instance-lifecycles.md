# The hub owns managed instance lifecycles

The hub owns container creation, starts, stops, removal, and explicit updates for each instance it manages. Adopting an existing instance transfers control from its previous manager while preserving its directory and volumes. Compose and a host update cron must not compete with the hub for that instance, and two runtime processes must never write to its data at once.

Each instance keeps its selected version until the user requests an update. A normal stop waits for active work to finish, with a separate force option. Removing an instance preserves its data unless the user explicitly requests deletion. After a machine restart, instances recover their intended running or stopped state.

For hub-managed instances, this decision replaces the host cron update policy in [The coder runs on a public box behind Caddy](0030-the-coder-runs-on-a-public-box-behind-caddy.md). Existing deployments keep that policy until adoption transfers ownership. The hub registry records management state, while each instance's manifest remains the source of its behavior configuration.

Decision agreed during [Hub and instance lifecycle](https://github.com/jorgesolerrr/kinby/issues/215). The [hub lifecycle spec](https://github.com/jorgesolerrr/kinby/issues/235) defines the migration steps and failure handling.
