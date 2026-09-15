# The hub owns lifecycle state in SQLite

The single-user hub stores managed instances, image artifacts, storage inventories, and lifecycle operations in SQLite under the hub directory. Manifest files remain the source of behavior configuration. Container effects cross one async container-runtime protocol; the Docker implementation keeps host-path translation, labels, and Docker identities behind that boundary.

Lifecycle mutations are serialized per hub instance ID. The hub records intent before an external effect and records the observed outcome afterward. Operations are independent of thread turns and remain inspectable after the requesting client disconnects or the registry is reopened.

## Consequences

- The hub is the one writer for management state and needs no database service.
- Instance agents receive no hub scopes; contract authorization runs before payload validation or effects.
- Stopping the hub does not stop containers. Recovery inspects registry intent and labeled runtime resources rather than booting instances in the hub process.
