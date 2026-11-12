# A factory runs on a workbench, and an instance owns its lines

Supersedes [ADR 0084](0084-an-instance-update-syncs-the-files-its-factorys-template-supplied.md) and [ADR 0090](0090-an-instance-picks-its-factory-role-and-owns-its-runs.md). Amends [ADR 0075](0075-factories-are-hub-run-and-own-their-runs-state.md) and [ADR 0106](0106-each-factory-run-works-in-its-own-run-worktree.md).

A factory's steps ran inside an instance, so one instance was four things at once: the place the work happens, the factory's hooks and tools, the behavior of each step, and a teammate with a persona, threads and memory. The playground hub showed the cost. `software-coder`, `cec-backend` and `landing-coder` held the same template, file for file, and differed in their secrets and four manifest values: the repository, two lists of repository commands and the model. Each still appeared as a teammate nobody talks to. The `course` builder could neither hand work to its own factory nor send a run back, so its prompt told the user to do both by hand.

## Decision

- **Steps run on a workbench, which is not an instance.** A workbench is durable and holds an image, secrets, named values, a workspace with its repository commands, its skill picks and a browser session. It has no persona, threads or memory.
- **A run works on exactly one workbench, in a folder kinby cuts for it.** For a git workspace that folder is the run worktree. A factory no longer writes a step to prepare it. Steps run one at a time per workbench.
- **A line is a factory set up on one workbench under an owning instance.** Setting it up checks that the workbench meets what the factory requires. Runs belong to the line. A workbench can carry several lines, and an instance can own several.
- **A factory holds its steps, prompts, hooks and tools, and nothing copies them.** It declares what it requires of a workbench: an image recipe, secrets, named values, lists of repository commands, logins and a browser. Factory roles, instance templates and the template sync of an instance update are gone.
- **Any instance can own a line, and the home instance does by default.** The owner gets the run stops, hands work in from a chat turn, reads its workbenches, and holds the memory of their runs, each fact and episode marked with its workbench. A run recalls its own workbench's memory and the unmarked rest.
- **The factory declares its intake, and the owner's routine fires it.** Setting up a line creates that routine in the owner, which keeps the schedule and receives the deliveries. The code that finds work items runs on the workbench, where the secrets it needs are.
- **A step that runs a model is an agent step, with an engine.** The engine is a kinby turn or a coding client, so the `agent` and `client` kinds become one, `agent`, with an `engine` field. An agent step is a turn of its run and shows under the run, in no instance's threads. It may name its model, and otherwise takes the line's. The daily budget is the line's.
- **Any step may carry a check, and a failed step can run again with what failed.** The check replaces the required hook and the code step that followed most agent steps. Send-backs stay bounded.
- **An approval notifies by itself**, and its summary takes the run's values. Notes for a redo travel with the send-back as a run value. A view a step writes belongs to the owner.

## Considered options

- **A slimmed-down instance as the place steps run.** Rejected. It keeps a chat and a memory per repository that nobody uses, and keeps five entries in the list of teammates.
- **A fresh place per run, or per step.** Rejected. The `course` factory needs a browser session that stays signed in and drafts the user reads between steps. The `software` factory resumes one coding session across steps, in the directory it started in, with the installs it made.
- **One workbench holding several repositories, or a run that moves between workbenches.** Rejected for the reason ADR 0090 gave: one container would hold several repositories' secrets and dependencies. A change that spans two repositories is two work items.
- **The hub owns runs and intake, with no instance.** Rejected. ADR 0075 kept triggers in routines so the hub needs no scheduler or receiver of its own.
- **A hand-in names its factory and workbench, with no line.** Rejected. A missing secret would show only when the first work item arrived, and the intake routine and a webhook's address would have nothing to belong to.
- **Each step names its skills in the factory.** Rejected, as in ADR 0090. Skills are a repository's taste, like its commands, so the workbench holds the picks and the factory only suggests.
- **Running both models side by side during the move.** Rejected. It means two ways to run a step.

## Consequences

- Each instance that picked a factory role is converted once into a workbench and a line owned by the home instance. Its memory moves to the owner marked with that workbench, and its threads stay readable. Its unfinished runs finish or are cancelled first. Each conversion ends with a real work item run on the new line, compared with a run from before.
- Changing a line's owner means moving the memory marked with its workbench.
- The instance list holds teammates only. Factories, lines, workbenches and runs get an area of their own in the web app ([#1299](https://github.com/jorgesolerrr/kinby/issues/1299)).
- A second factory that requires another image recipe cannot be set up on a workbench that already has one.
- The owner reads a workbench but writes to it only through its record, such as the repository commands. General write access waits for something that needs it.
