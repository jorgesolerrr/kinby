# Factories are hub-run and own their runs' state

A **factory** is a declared sequence of **steps** that carries a **work item** from start to finish. Its steps can run in different **instances**. The **hub** runs it: it holds the factory files, stores each **factory run** (the current step, the **step results** so far, and the attempts per step), and wakes each step's instance over the contract it already uses. An **intake** routine in one of the factory's instances starts runs. A step that waits on the outside world parks the run in the hub until a matching **signal** arrives or a deadline passes.

Factories live in kinby, as files on the hub. Anyone who deploys kinby writes their own, directly or through the home instance under the gate. kinby ships the **software factory** as an editable starting point. A factory replaces the package. Its folder holds the factory file, its prompts, and an **instance template** per instance. No factory has a repository of its own, so `kinby-code-factory`, the curated list, package pins and `package.yaml` retire. This supersedes ADR 0029's stateless re-entry for factory work and ADRs 0040, 0055, 0056, 0058, 0059 and 0061.

## Considered options

- **A lead instance owns the factory and delegates to the others.** Rejected. One instance would coordinate its peers, and the hub would have to let instances wake each other. The hub already creates, wakes and stops instances.
- **A run ends with its wake, and every wake works out positions from the outside system again** (ADR 0029, how the coder works today). Rejected. Once work passes between instances, a run spans several turns in several places, and the handoff and the failure count need somewhere to live between them.
- **A factory owns its own triggers.** Rejected. The hub would need its own signal receiver and scheduler, duplicating what each instance has. Routines keep every trigger guarantee they have.
- **A graph with conditions and parallel branches.** Rejected. Steps run in a line, and a declared outcome sends the work back to an earlier step at most N times. That covers the coder, and a graph is a workflow engine kinby does not need.
- **Factories as packages in their own git repositories.** Rejected. A repository per factory kept the coder's logic out of reach of the user and the home instance, and pinning it took a release process of its own.

## Consequences

- A factory's instances are ordinary instances, in the sidebar, with their own memory and threads.
- An instance takes one step at a time. Runs queue per instance, first in, first out, and a parked run holds nothing.
- Steps in different instances hand repository work off through git, never a shared workspace.
- A step that runs out of retries or send-backs leaves the run **needs human**, keeping everything done so far. The user retries the step, sends the run back, or cancels it. A failed run is no longer a completed turn the routine failure policy cannot see.
