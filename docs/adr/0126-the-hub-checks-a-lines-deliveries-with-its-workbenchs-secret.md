# The hub checks a line's deliveries with its workbench's secret

Amends [ADR 0125](0125-a-factory-runs-on-a-workbench-and-an-instance-owns-its-lines.md).

ADR 0125 put a line's intake in a routine of its owner, which received the deliveries and checked each against the secret its factory's intake names, read from the owner's environment. One factory names one variable, so an owner of two `software` lines whose repositories sign with different webhook secrets could hold only one of them. A conversion left the secret on the workbench and wrote the intake into the home instance, which held none. On the playground, the CEC instance planned in [#1338](https://github.com/jorgesolerrr/kinby/issues/1338) owns two lines that sign apart.

## Decision

- **The line's workbench holds its delivery secret,** under the name the factory's intake declares. Setting up a line asks for it, as it asks for any secret the factory requires. Setup and conversion refuse a workbench without it and name it.
- **The hub checks each delivery to a line's intake** against that secret, whichever path the delivery came in on, and rejects one that fails.
- **The owner takes a checked delivery on its control token,** which the hub adds when it hands the delivery on. The intake routine names that token as its signal's secret, so the owner holds none of the line's secrets.

## Considered options

- **Each line's secret in its owner, under a name of the line's own.** Rejected. An instance's environment is fixed when its container is created, so each setup, conversion or change of owner would recreate the owner, the home instance included. It would also add a secret to move with every change of owner and to remove with the line.

## Consequences

- A conversion changes nothing on the sender's side: the instance's secret stays where it was, on the workbench.
- Changing a line's owner moves no secret, and removing a line leaves the workbench's secrets as they are.
- The workbench's container holds the delivery secret, which its steps never need.
- An intake routine written before this change names the factory's secret in the owner, and rejects the deliveries the hub now hands on. Removing the line and setting it up again rewrites it.
