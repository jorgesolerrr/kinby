# An instance picks its factory role and owns its runs

Amends [ADR 0075](0075-factories-are-hub-run-and-own-their-runs-state.md).

A hub installed a factory once, and the install created the factory's instances. A second coder for another repository needed a copy of the whole factory folder under another name. The hub's `cec-agent` factory is such a copy of `software`, and it stopped following the shipped one. The folder also carried one coder's choices: the eight skills in its template and the prompts that named them ([#594](https://github.com/jorgesolerrr/kinby/issues/594)).

## Decision

- **Creating an instance is the only entry point.** Every instance goes through the same **onboarding**. Working in a factory is one of its **picks**: the instance picks a **factory role**, and the hub copies that role's **instance template** into it. Installing a factory as a separate act is gone.
- **At most one role, fixed at creation.** A role brings hooks, tools, an intake routine and runs in flight. Changing it means creating another instance.
- **A run belongs to the instance that handed in its work item.** The duplicate work item check, the needs-human count and the run list are per instance. Two coders can each carry issue 12 of their own repository.
- **A factory holds behavior only.** Its steps, prompts, hooks and template defaults stay. Nothing in it names one repository's commands or conventions, or a picked skill. A client step's prompt receives the instance's skill list, as a turn does.
- **A factory with instances cannot be removed.** The hub refuses and names them. Removing an instance is the only removal that touches an instance.

## Considered options

- **Several installs of one factory**, each a named set of instances. Rejected. It keeps two ways to create an instance and adds an identity nobody asked for. An instance that picked a role already says which factory it works in.
- **One coder serving several repositories.** Rejected. One container would hold several repositories' secrets, workspaces and memory.
- **The factory declares the skills its prompts need, and the factory check fails an instance that lacks one.** Rejected. Two coders with different test-first skills would need a mapping from what the prompt names to what each coder carries. The model already picks a skill from its name and description.

## Consequences

- A coder with no test-first skill does no test-first work, and nothing warns about it. The user picked its skills.
- "Install a factory" leaves the hub's methods and the web app's wording.
- A factory with two roles does not say which reviewer works with which coder. The `software` factory has one role, so this waits for a factory that needs it.
- How a repository's own commands and conventions reach its coder is [#595](https://github.com/jorgesolerrr/kinby/issues/595).
