# Babysitting accepts feedback from trusted authors

The coding client can run commands in the factory container. Public review comments must not grant that access to arbitrary contributors.

The babysitter accepts automatic fix requests only from the repository maintainer and the production Greptile app. Other authors require a human. The check runs before the coding client starts and covers every comment included in its prompt.

This trusts the maintainer and Greptile to supply review feedback. It does not isolate a compromised reviewer or make repository code safe to execute. The factory container remains the execution boundary described in ADR 0029.
