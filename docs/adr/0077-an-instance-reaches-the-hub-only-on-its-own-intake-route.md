# An instance reaches the hub only on its own intake route

Until factories, the hub called its instances and no instance called the hub. Intake changes that, because an **intake** routine has to hand the hub each **work item** it finds. An instance now calls the hub on one route, `/instances/<id>/intake`. It presents the control token the hub gave it, and the hub checks that token against the instance the path names. That route serves one contract method, `factory.run.intake`, under the `factory:intake` scope. The hub binds the call to that instance, so an instance can start runs only of the factory whose intake it is. The user's access token does not hold that scope.

The hub writes the route's address into each factory instance's environment as `KINBY_INTAKE_URL`, built from `kinby hub --private-url` (default `http://hub:8080`, the compose service). Only a factory's instances get it, and the `hand_to_factory` tool exists only where it is set.

## Considered options

- **Any instance's control token opens the hub's `/ws` with an intake scope, and the call names its instance.** Rejected. An instance could then start runs of a factory it is not the intake of, and instances stay isolated from each other.
- **A token of its own for intake.** Rejected. The control token already proves which instance is calling, so a second secret per instance would protect nothing more.
- **The hub polls each intake instance for new work items.** Rejected. It adds a loop and a delay to every factory, and routines already decide when work comes in.

## Consequences

- The control token now proves the instance to the hub as well as the hub to the instance.
- The intake route carries plaintext over the private network, as the control socket does.
- Instances created before this have no intake URL. Only factory instances need one, and factories are new.
