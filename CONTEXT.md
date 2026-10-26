# Context — kinby

Glossary of the ubiquitous language. Terms only — no implementation detail.

## Terms

### kinby

The project's name: an open-source, self-hosted personal AI teammate. An invented word — *kin* + *by*, kin at your side. Always lowercase; the same single word names the GitHub repo, the PyPI package, the Python import, and the CLI command. Refers to the *software project*, never to an individual running instance's persona.

### Persona name

The per-instance display name a user gives *their* running agent ("call it whatever you like"). A configuration field, chosen by each self-hoster; deliberately distinct from **kinby**, which names the project itself.

### Short-term memory

The recent past: the current and previous sessions, recalled by recency. One of the three memories, alongside **long-term memory** and **reasoning traces**.

### Long-term memory

Durable knowledge the agent keeps about its user: the **profile** plus the **knowledge graph**.

### Profile

The human-legible record of the user's preferences, persona settings, and standing instructions. The only memory that is always present in the agent's context; directly editable by the user.

### Knowledge graph

The life wiki: entities, episodes, and time-stamped facts about the user's life — drawn from documents, emails, and other content the user grants the agent, plus facts distilled from conversation. Queried by the agent on demand (never auto-injected); answers "search my life" and "what happened on X day". Retires the earlier term *memory graph*.

### Episode

One distilled record in the **knowledge graph** of a single **turn** or **factory run**: what happened, what was decided, what should have gone differently, and the tools and path the agent used. Written by the **recap** and lets later work reuse or improve that path. Episodes are the raw material **dreaming** turns into **facts**: a search returns them only when it is bounded by date or no fact matches, and an old episode is **pruned** once it is **covered** and no fact cites it. A **turn** with **no work** writes none. A factory run writes one when it ends, and its steps write none of their own.

### Fact

An atomic, timestamped statement in the **knowledge graph**, recorded when it is learned. Recency decides which fact is current: the latest fact about a subject wins. The agent remembers a fact in a **thread**; the user can add one from a client, and that fact belongs to no thread. A fact **dreaming** writes cites its **source episodes**, the episodes it was learned from. A search finds such a fact by its own words or by those of its source episodes.

### Correction

The user replacing a **fact** they judge wrong: a new fact, dated the day of the correction, plus a **tombstone** on the old one. Nothing in the graph is rewritten in place. An **episode** has no correction; it can only be forgotten.

### Dreaming

An **instance**'s periodic review of its own memory, run in **stages** on different cadences, each a **routine** with its own lens. Each stage leaves a **dream report**. Dreaming never rewrites, merges or tombstones an episode, never writes back a fact the user forgot, and never edits the **profile**.

### Dreaming stage

One cadence of **dreaming**: **consolidation** nightly, **reflection** and the **learning review** weekly. An instance can add its own, such as a monthly one.
_Avoid_: dream phase

### Consolidation

The nightly **dreaming stage**. It reviews uncovered **episodes** and writes a **fact** for a lesson or detail at least two episodes repeat, or an **area fact** for an area they share, citing them as its **source episodes**. A source may be an earlier episode on the same subjects that it covered on another night and no fact cites. Its own write needs no approval.

### Reflection

The weekly **dreaming stage**. It looks across the period's facts and episodes for patterns in how the user works and makes **proposals**.

### Proposal

A change **reflection** suggests from a pattern it found: a **fact**, a **skill**, or a **routine** without a **code step**. Nothing a proposal adds is code. It is applied only through the gate.

### Learning review

The weekly **dreaming stage** that judges whether the **instance** learned from its memory. It reads the week's **turns** and **factory runs** that went badly, next to the **facts** that matched them and what each one **recalled**, and names every **repeated mistake** and **ignored lesson**, citing the turn or run and the fact. It writes nothing to memory and makes no **proposals**; its **dream report** is read by the user, who rates it. A week with nothing gone badly costs nothing.
_Avoid_: judge, audit

### Repeated mistake

A **learning review** verdict: a **turn** or **factory run** went wrong in a way a **fact** already described, and did not **recall** that fact.

### Ignored lesson

A **learning review** verdict: a **turn** or **factory run** **recalled** a **fact** and then did what the fact warned against.

### Dream report

What a **dreaming stage** leaves on its **thread**: the facts it wrote, with their sources, and its **proposals**. The user acts on it by replying in that thread.
_Avoid_: dream artifact

### Area fact

A **fact** **consolidation** writes about one area of the work, such as a feature of a repository: where it lives and what was decided about it, and why. When a later **episode** touches the area, a new area fact citing the old sources and the new one takes its place.

### Source episode

An **episode** a **fact** cites as the evidence it was learned from. While a live fact cites it, an episode is never **pruned**.

### Covered

Said of an **episode** that **consolidation** has reviewed, whether or not it wrote a fact from it. Only a consolidation that finished its writes covers an episode; a failed one leaves its episodes uncovered for the next run.

### Prune

Deleting an **episode** that is older than three months, **covered**, and cited by no live **fact**. Distinct from forgetting: a prune leaves no **tombstone**, and the **turn** it came from keeps its recap record, so nothing re-derives it.

### Transcript store

The canonical record of every conversation with the agent. The knowledge graph is derived from canonical sources like this one, never the reverse.

### Reasoning trace

The third memory records the agent's reasoning and actions for a task. The **transcript store** holds its canonical raw form, while an **episode** holds its distilled form in long-term memory.

### Memory facade

The single interface through which kinby recalls, opens, remembers, and forgets. Core callers and **memory tools** share one implementation and stay independent from the **feeds** behind it.

### Feed

One source of memory behind the **memory facade**, such as the **profile**, the **knowledge graph**, or a RAG index over the **transcript store**. Each feed must pass its evals before kinby adds the next one.

### Eval

An offline run of fixed cases that measures an **instance** or one of its parts against reference answers under a named model. Paid for and run on demand; never part of ordinary use.

### Feed gate

The correctness and memory-token thresholds a **feed** must pass before kinby builds the next one.

### Memory tool

A core **tool** that exposes the **memory facade** to the model. Search and open are read tools, while remember and forget are write tools governed by the **gate**.

### Ingestion pipeline

The path content travels into the knowledge graph: the user drops or uploads an item, or an integration delivers one, and the pipeline turns it into graph knowledge.

### Backfill

A user-initiated, explicitly scoped ingestion of historical content (a folder, a date range, a label) — as opposed to the default incremental ingestion of new items as they arrive.

### Tombstone

The suppression marker a forget leaves behind. The **knowledge graph** is derived from canonical sources, so deletion alone would let a fact resurrect on re-ingestion; the **ingestion pipeline** never re-derives a tombstoned fact. The **transcript store** itself is never rewritten.

### Instance

One kinby deployment: a directory (and, when deployed, a container) that owns its behavior configuration, memory and transcripts. One instance serves one user with one persona. Every instance has the same shape; a repo-scoped coding agent is an instance whose workspace is that repo, not a different kind of instance.

### Hub

The single-user service that manages **instances** and provides their shared entry point for the user. An instance can keep running while its hub is stopped.

### Instance registry

The hub's record of the **instances** it manages, their identities, storage locations, selected versions, and intended running states. Each instance's **manifest** remains the source of its behavior configuration.

### Home instance

The one **instance** the **hub** creates for itself and the user talks to about kinby: it builds inside kinby on the user's behalf and does personal work itself. The hub creates it on start whenever it holds none, as a **vanilla instance** whose API key waits as **pending setup**. The **instance registry** marks it, never its own **manifest**. Its **persona name** defaults to "Kinby". It can be stopped and renamed, never removed, and the role never moves to another instance. The web app lists it first and opens on its newest thread.
_Avoid_: system agent, main instance, Kinby (as a term)

### Hub instance ID

The stable identity of one entry in the **instance registry**. It is distinct from the instance's own manifest ID and **persona name**, so adoption preserves both.

### Instance secrets

The environment values one managed **instance** holds, kept in a protected file beside that instance and never in the **instance registry** or the **hub**'s own environment. A container reads them once, when it is created, so replacing them changes nothing until a **container recreation**.
_Avoid_: credentials, env vars

### Hub secret

A named value the **hub** keeps so that several **instances** can take it for a secret **setup field** instead of the user typing it again. The hub keeps it in a protected file of its own, never in the **instance registry** or its own environment, and never shows it back. An instance that takes one stays linked to it: the value is written into that instance's **instance secrets**, and replacing the hub secret rewrites them, which reaches the instance as a **recreate reason**.
_Avoid_: secret repository, vault, credentials

### Channel

A place outside kinby where the **hub** sends the user **notifications**: a Telegram chat, a Discord channel, an email inbox. The hub owns every channel, for all its **instances**, and sends each notification to every channel that has not muted its instance. A channel answers only to the user it was paired with, and some channels carry the user's **reply** back.
_Avoid_: destination

### Channel kind

The sort of service a **channel** talks to, such as Telegram, Discord or email. A new kind is added without changing kinby's core. A channel is one configured use of a kind.

### Notification

What kinby sends the user through its **channels** when something needs them or an **instance** decides to tell them. kinby sends one by itself when an **approval** waits or a **routine notice**, an **instance notice**, or a **factory run** that **needs human** appears. An instance's **turn** sends one only when it chooses to, so a turn that sends none is silent. Every notification names the instance and where it came from, and links back to its **thread** or run. It can offer options to **reply** with.
_Avoid_: message, alert, push

### Reply

The user's answer to a **notification**, sent back through its **channel**: for now, one of the options the notification offered. A reply to kinby's own notification answers the **approval** or the **factory run** it was about. A reply to an **instance**'s notification starts a user **turn** in the **thread** that sent it. A reply to something already settled changes nothing.
_Avoid_: pick (a **pick** is a library choice), response

### Lifecycle operation

One tracked attempt by the **hub** to change an **instance**, such as creating, starting, stopping, updating, or removing it. An **image preparation** is one too, and it belongs to no instance. A **subscription login** is one too, and it runs beside the instance's other operations. Its identity and outcome remain available after the requesting client disconnects. One the **home instance** asked for records it as the operation's requester.

### Operation step

One recorded stage of a **lifecycle operation**, kept in the order the **hub** reached it. A client polls the steps to follow an operation that outlives its connection; there is no operation stream.

### Force stop

A user's request to stop an **instance** without waiting for its accepted work. It interrupts running **turns** and parked **approvals** through the **instance runtime**, then takes the container down within a bounded grace period. A running instance whose lifecycle endpoint does not answer is terminated without a drain. A force stop may also escalate an **instance draining** that is already pending, inside whichever **lifecycle operation** is draining, and that operation carries on.

### Container recreation

The **lifecycle operation** that replaces one **instance**'s container with a new one built from the **image artifact** and **instance secrets** already recorded for it. It is how replaced secrets reach a running instance, and the only way a container the **hub** has lost comes back; it never selects a different image and never touches the instance's storage.
_Avoid_: rebuild, restart, update

### Recreate reason

A change that reaches a running **instance** only through **container recreation**: replaced **instance secrets**, or an edited configuration file the container reads only at start. The user sees every pending reason together and applies them with one recreation. A stopped instance applies them when it starts.

### Config change

One recorded edit to an **instance**'s configuration: which file, who made it (the user from a client, the agent through an **instance tool**, or the **failure policy**), when, and what changed. Config changes are kept apart from any **thread**.
_Avoid_: config event, audit entry

### Instance update

The **lifecycle operation** that moves one **instance** onto the **image artifact** a newly selected revision prepares. The candidate is prepared before the current container is disturbed, so a preparation failure leaves the instance running on its selected image. The replacement then takes the drain-then-stop path and keeps every storage the instance owns. For a **factory**'s instance it also syncs the files its **instance template** supplied: it writes the ones the factory changed, removes the ones the factory dropped, and keeps and lists any the instance changed since. It never touches the **manifest**, the **instance secrets**, memory, **threads**, or a file the template did not supply. A replacement that does not come up is a failed operation, never an automatic return to the previous image.
_Avoid_: upgrade, rollout

### Instance notice

Something the **hub** reports about one **instance** that the user may want to act on, without stopping it: the instance runs another kinby revision than the hub's checkout, the **instance template** its **factory** serves now differs from the files the **hub** recorded when it copied them, or the instance's environment lacks the hub route and the scopes the registry grants it. An **instance update** answers all three.
_Avoid_: warning, alert

### Instance removal

The **lifecycle operation** that takes one **instance**'s container down the drain-then-stop path and removes it. The instance's directory, named volumes, **instance secrets**, selected **image artifact**, and **instance registry** record all stay. A removed instance leaves the active list, but its record stays readable and its storage stays reserved until the instance is restored or permanently deleted.
_Avoid_: delete, uninstall

### Instance restoration

The **lifecycle operation** that brings a removed **instance** back from its retained record. It checks the retained configuration, storage and selected image, then creates a container under the same **hub instance ID** and leaves it stopped. It reports a missing volume or image and never replaces it with a new one.
_Avoid_: undelete, recreate

### Instance deletion

The **lifecycle operation** that permanently deletes a removed **instance**'s owned storage: the directories and named volumes its **storage inventory** records as writable, and nothing its **manifest** merely references. A preview lists those targets first, and names any writable bind the hub cannot see as a retained bind, which stays on the Docker host. The deletion checks them again and refuses if they changed or if another record shares them. Each target leaves the inventory once it is gone, so a retry continues with what is left. Once nothing owned remains, the hub marks the record deleted and it leaves both lists. Its operations stay readable. The path and container name stay on that record, and both are unique, so adopting a replacement that reuses either one is a blocking adoption finding.
_Avoid_: purge, destroy, remove

### Lifecycle recovery

What the **hub** does with each managed **instance** when it opens its directory: it reads the **instance registry** and the containers labeled as its own, then restores the intended running or stopped state of the containers that are still there. It reports everything else — a missing container, an unfinished **lifecycle operation**, a container it does not own — and changes nothing.
_Avoid_: reconciliation, adoption, self-healing

### Adoption

The **lifecycle operation** that puts an existing **instance** under a **hub** without moving its data. The hub keeps the instance's manifest id, persona name, bind locations and named volumes exactly where they are, gives it a **hub instance ID**, and replaces its container with one it labels itself. The previous manager relinquishes control first, and the hub rewrites no Compose file.

### Adoption preflight

The read-only preview of an **adoption**: the identity, directories, **storage inventory**, image and current container owner the hub observed, what the handoff will cost, the steps the operator takes first, and every **adoption finding** that would stop it. It changes nothing, so it can be run as often as the operator likes.

### Adoption finding

One thing an **adoption preflight** observed about an instance and its container, such as a manager that has not let go, a runtime that cannot drain, or storage another record already owns. A blocking finding stops the handoff; the rest are there to be read.

### Image artifact

An immutable container image together with the resolved source revision, dependency identity, and base-image identities that produced it. An instance keeps its selected image artifact until an explicit lifecycle operation changes it.

### Image preparation

The **lifecycle operation** that builds or reuses the **image artifact** for one of the image recipes kinby ships, before any instance exists, and stores what the image declares with the artifact. Preparing a selection that is already being prepared returns the preparation that is running.
_Avoid_: image build, warm-up

### Setup field

One value a prepared image asks for before an **instance** is created from it: a configuration value or a secret. Each has a type (text, multiline, boolean, integer, choice, email, or URL), may be required, and may have a default. An email is one address with a dot in its domain, and a URL has a scheme and a host. A secret never has one. The built-in fields come first. Every instance asks for its model, its API key and its **behavior prompt**, which starts from kinby's default or the **instance template**'s. kinby writes those itself. An **instance template**'s own fields follow, and each of its configuration fields names its target: a file of the instance and a dotted key inside it. A template may give a built-in field another default, but it cannot remove the field. Defaults resolve built-in first, then template, then the user's value. A template's required secrets read as required secret text fields.
_Avoid_: required input, form field

### Subscription login

One sign-in an **instance template** declares for a command-line agent its **instances** run on a subscription: the command that signs in, where its credentials live, and how to find a URL and a one-time code in what the command prints. The **hub** runs it in a **setup container**, and the user finishes it in a browser. Each one keeps its credentials in a named volume the instance owns.
_Avoid_: login, device flow, auth

### Setup container

The temporary container the **hub** runs on an instance's **image artifact** for one **subscription login**. It runs the login's command in place of kinby, holds none of the **instance secrets**, and is removed when the login ends, however it ends. The command signs in on an empty scratch home at the path where the login keeps its credentials. The container mounts only that login's volume, at `/kinby/login`, and the files the command wrote are copied into it only when the command succeeds, so a failed or expired sign-in leaves the credentials before it working.
_Avoid_: login container, helper container

### Pending setup

What an **instance** still needs after creation: a **subscription login** that is not signed in, a required secret **setup field** with no value in its **instance secrets**, or **repository commands** its **factory**'s steps name and it does not hold. The **hub** tracks each declared login per instance as pending, signed in, or failed, from its creation on, and reads the secrets from the instance's stored descriptor and its secrets file, never showing a value. An instance created before the hub tracked logins reads as complete. Pending setup never blocks a start. A stopped instance with pending setup opens on its setup card, where the user signs in, sees which secrets are set, and starts it.
_Avoid_: incomplete instance, onboarding

### Avatar

How clients draw an **instance**: a shape (circle, squircle, or square) and a color named from a fixed palette, which each client maps to its own theme. The user picks it at creation, and the **instance registry** keeps it, never the instance directory, so it shows before the instance ever starts. An instance nobody picked one for is a circle in the palette's first color.
_Avoid_: icon, logo

### Container runtime

The hub-facing interface that creates, starts, inspects, and reads logs from isolated instance containers. Docker is the first implementation; Docker-specific paths and identities stay behind the interface.

### Storage inventory

The hub's explicit record of storage owned by an instance. Each entry distinguishes a bind source or named volume from its container mount destination and records whether it is writable.

### Instance draining

The period when an **instance** stops accepting new work and waits for its running **turns** to finish before stopping. A parked **approval** is not waited on: it stays parked and resumes at the next start. Reads remain available, and so do **approval** responses until the running turns finish, while new **deliveries** wait for the next start.

### Instance discovery

The ordered lookup that selects an existing **instance** when a command does not name one directly. The matching rule is part of the result so kinby can tell the user why it selected that instance.

### Instance boot

The transition from a loaded **instance** to its live **instance runtime**.

### Instance runtime

The live state and background work of one booted **instance**, owned and stopped together. It can stop after a running **routine** finishes or interrupt that routine immediately.

### Serve mode

An **instance runtime** without a REPL, kept alive so the **scheduler** can fire **routines**.

### Runtime lock

The claim an **instance runtime** holds on its **instance**'s state directory for as long as it runs. A second runtime on the same instance refuses to start, because one process owns **event** appends.

### Manifest

The portable description of an instance's identity and configuration. It contains no secrets or runtime state.

### Workspace

The directory holding the user's *own* work that an instance acts on — a repository, a notes folder. Lives under the instance (cloned there in a container, linked there on a local install) and is never written to by kinby on its own behalf: the instance's behavior stays in the instance, though it may *read* the workspace's **conventions** as an additional behavior source. The **software factory**'s runs never work in it directly: each works in its own **run worktree** cut from it.

### Conventions

The workspace's own instruction files and skill directories an **instance** may read as extra behavior sources. Named explicitly in the **manifest**. The instance never loads tools from the workspace.

### Workspace snapshot

The recorded state of every file in the **workspace** at one **turn** boundary, ignored files excluded. Every turn has one from before it started and one from when it closed, so the difference between them is exactly what the turn changed.
_Avoid_: checkpoint (that is the **graph checkpoint**), commit

### Workspace diff

The file changes and unified patch between two **workspace snapshots**. A turn's workspace diff compares its starting snapshot with its closing snapshot, so it contains only that turn's changes.
_Avoid_: git diff (the workspace may not be a Git repository), changes (when the snapshot comparison matters)

### Workspace revert

A user's request to put the workspace back to the **workspace snapshot** taken before a chosen **turn**, discarding that turn's changes and every later turn's. Recorded on the thread like a turn, with its own snapshots, so a revert can itself be reverted.
_Avoid_: rollback, undo

### Turn target

A **turn** or recorded **workspace revert** selected for a **workspace diff** or **workspace revert**.

### Thread

One conversation with its own durable history. Survives across sessions; can be resumed later. What memory distills from and what **instance statistics** are derived from. Its title is the one it was created with, or else its first message cut to 60 characters, until the user renames it.

### Thread status

What a **thread**'s latest **turn** is doing: running while it is open, awaiting approval while it waits on an **approval**, failed when it closed as failed, awaiting reply when it closed after raising a **notification** with options the hub did not refuse, and idle otherwise, including after a **turn interruption**. Derived from the thread's events when it is read, never stored. Failed and awaiting reply clear when the next turn starts, as a **reply** starts one.

### Routine run

A **thread** whose first **turn** a **routine** started. It stays a routine run after the user starts a turn in it. Derived from that turn's **origin**, never stored.
_Avoid_: routine thread, job run

### Archived thread

A **thread** the user has put away. It leaves the sidebar and nothing else changes: its events, **episodes** and statistics stay, and the user can bring it back. Threads are archived, never deleted.

### Session

One run of the agent loop against a thread, from start to exit (a process, a REPL open–close). Ephemeral; the unit a server wraps. A session contains one or more **turns**. Not a model call: a turn makes one or more model calls, and a model-assisted **recap** makes one more after it.

### Turn

One cycle of agent work within a **thread**, started by the user or a **wake**, ending when it completes, fails, or is interrupted. The natural unit of token attribution, checkpoint bracketing, compaction boundaries, and eval cases. A turn is also the unit of work: what the user calls a task is a turn, and each turn earns at most one **episode**.
_Avoid_: task

### Budget

A ceiling an **instance** sets on one **turn** (steps, tokens, seconds) or on a UTC day (cost). Reaching it closes the turn as failed. Absent means unlimited; a **routine** may lower a budget, never raise it.

### Turn interruption

A user's request to stop the active turn before it completes.

### Turn rating

The user's good or bad verdict on one **turn**, with an optional reason. A later rating becomes the current verdict while earlier ratings remain part of the thread's history.

### Feedback policy

The **instance** setting that chooses whether to ask for a **turn rating** after every completed **turn** or never ask.

### Token usage

The input and output tokens attributed to a turn, with totals rolled up for its thread. Input tokens include the cached tokens the provider reports, recorded as a split so a lost prompt cache is visible.
`usage.get` includes completed turns whose closing timestamp falls within its **time range**, and lists beside each one its **delegated runs** whose own timestamp falls within it.

### Price map

The table of input, output, cache read, and cache write prices per million tokens, keyed by exact `provider:model` names.

### Model choice

A model a client may pick for an **instance**'s main, recap, or embedding model. Only a model the **price map** prices is one, so the daily budget can always count it. Its price comes from kinby's shipped prices or from the **manifest**'s own, and it says whether its provider's `<PROVIDER>_API_KEY` is set.

### Usage source

The account that pays for a model's tokens: the API, a Claude subscription, or a ChatGPT subscription. Only API use is priced; subscription use is counted.
_Avoid_: provider, client

### Plan window

The rolling period over which a subscription limits use, such as five hours or one week. The window belongs to the account, so every **instance**, and any use outside kinby, draws from the same one.

### Delegated run

One run of an outside agent on a subscription **usage source**: started by a **tool** during a **turn**, or by a **step** that runs a **coding client**. It is reported with its tokens, duration, and outcome. A tool's run sits beside its turn's **token usage**, never inside it; a step's run belongs to no turn. A run stopped by the subscription's limit is limited, and records when its **plan window** resets.
_Avoid_: subagent, model call

### Daily cost

The priced spend attributed to turns closed during one UTC day.

### Time range

Optional, inclusive `since` and `until` bounds applied to a timestamp. `usage.get` and `stats.get` both apply it to a turn's closing timestamp, and to a **delegated run**'s own timestamp.

### Turn metrics

The derived record of one closed **turn**. It describes the outcome, duration, tokens, tool and memory calls, approvals, current **turn rating**, and the turn's **delegated runs**.

### Instance statistics

Per-turn measures of one **instance** aggregated by UTC day or week. Derived from the **transcript store** through **turn metrics**, `kinby stats` recomputes the totals and writes `stats.json`.
Each bucket and the total split by **usage source**: API use keeps its tokens and cost, and each subscription counts its **delegated runs**, tokens, and duration. A turn lands in the bucket of its close, a delegated run in the bucket of its own timestamp.
They also count each subscription's **delegated runs** in its **plan windows**, the five hours and the seven days up to now, and name its active limit: the latest limited **delegated run** whose reset is still ahead. Both ignore the **time range**, so a run or a limit reported before `since` still counts.
The **hub** adds them up across its running **instances** on request: each instance's buckets under its **hub instance ID**, the totals per **usage source**, the runs per **plan window**, and the latest reset per source. It stores none of it, and names every instance it left out because it was stopped, did not answer in time, or runs an older core whose answer it cannot read.

### Model call

One request to a model during a **turn**, recorded as an event with its tokens and duration. A turn's **token usage** is the sum of its model calls; the closing event carries that sum.

### Model call mismatch

A **turn** whose recorded **model calls** do not sum to the **token usage** on its closing event.

### Recall

The record that a **turn**, or a **coding client**'s session inside a **factory run**, read given **facts** or **episodes** from memory. It ties work to the memory it used.

### Memory tokens

What memory costs a **turn**, measured from the provider's own token counts. Two numbers: the size, the tokens memory added to the context once; and the carried tokens, that size re-read by every later **model call** in the turn. The **profile** counts by its size on every turn.

### Navigation

The read-only **tool** calls and model reasoning a **turn** spends locating what it needs in the **workspace**. A turn's navigation records all read calls and their duration. It also records the read calls before the first write, distinct paths, repeat opens, tokens before the first write, and write calls. The write count identifies turns that acted. Memory tools and the skill tool are not navigation. Its trend for one workspace shows whether memory is teaching the agent the workspace.
_Avoid_: exploration, workspace search

### Prompt version

A hash of the rendered **system prompt** without the **environment block**, recorded when a turn starts. Groups turns by the prompt the user wrote.

### Profile version

A hash of the **profile**'s text, recorded when a turn starts. The provider's tokenizer counts a version once, at the first turn that runs with it, and that count is the profile's size in every such turn's **memory tokens**.

### Eval arm

One way of preparing the same **eval** case for comparison. The memory eval has a graph arm, which uses the case's **knowledge graph**, and a stuffing arm, which puts the case's raw transcript in the **profile** and removes the graph.

### Turn runner

The part of the runtime that produces the agent's response and the turn's events.

### Recap

The retrospective work that runs after a **turn** or a **factory run** ends. It records the tool path and may use a model to distill the turn into an **episode**; it never delays the next turn or writes **facts**.

### Session digest

What the **recap** of a **factory run** reads from a **coding run**'s session: the files read and changed, the commands that failed, and the client's own prose. Tool outputs and file contents are left out.

### Recap policy

The **instance** setting that chooses a model-assisted **recap** after every turn or a trace-only recap.

### Recap prompt

The instance's own instructions to the **recap** model: what a retrospective means for this agent. Kinby supplies the frame and a default lens; the instance may replace the lens. A **behavior prompt** for the recap.

### Graph checkpoint

The turn runner's durable working state for a thread. It lets a parked approval resume after a process restart and carries completed thread state across turns. The **transcript store** remains the canonical conversation record.

### Approval

A user decision a live turn waits on before it continues. Requested as an event; answered through the contract. A parked turn stays live until the user answers or the turn is interrupted.

### Approval decision

The user's answer to an **approval**: approve, or deny with an optional reason. The model reads the reason in the denied call's result, so it can do what the user asked instead.

### Gate

The check every **tool** call passes through before it runs. It reads the tool's write flag and the instance's permission policy, and answers allow, ask (raise an **approval**), or deny. The policy is the instance's ceiling; a **thread** may narrow it, never widen it.

### Gate rule

The identifier for the policy condition that produced a **gate decision**, such as `mode.ask.write` or `bash.deny[0]`.

### Gate decision

The **gate**'s final verdict on one **tool** call, recorded as an event: allow or deny, the rule that decided it, and whether policy or the user decided. An **approval** the user refuses is a deny decided by the user.

### Sandbox

The isolation boundary around an **instance**. When deployed, the container is the hard wall: the worst a runaway command can destroy is the container itself. Everywhere, the **gate** is the soft wall. A new instance means a new sandbox.

### Permission mode

A named preset of **gate** rules a **thread** runs under: read-only, ask, auto, or full-access. The instance sets the default for new threads and the ceiling; a thread may pin any mode up to the ceiling, never above it.

### Denylist

The instance's list of command patterns the **gate** refuses or escalates regardless of **permission mode**. The patterns kinby ships always apply, and the instance's own patterns add to them. A tripwire, not a wall: it catches obvious disasters, while the **sandbox** provides the actual isolation.

### Contract

The typed set of commands and subscriptions every **client** uses to drive a session or manage **instances**. Clients import contracts, never core.

### Event

One sequence-numbered record in a **thread**'s durable history. Events record turn activity and durable thread state. The event stream is what clients subscribe to and what the **transcript store** persists; replaying it reproduces a thread.

### Scope

A named permission a **contract** command requires of its caller. Holding a connection is not permission to call everything.

### Client

A program that drives kinby through the **contract**. The REPL, the web app, and the management CLI are clients. A client in the same process calls the contract directly; any other client goes through a **contract server**.
_Avoid_: coding client (a different term), frontend

### Contract server

The part of **serve mode**, and of the **hub**, that carries the **contract** to network **clients** and authenticates each connection. It decides which **scopes** a connection holds. The hub's contract server also relays a client's connection to one **instance**. Separate from the **receiver**, which accepts **signals**.
_Avoid_: gateway, API server

### Frame

One message a **client** and a **contract server** exchange over a connection. A client calls, subscribes, or cancels; the server answers with a result, an error, a subscription's acknowledgement, its items, and its end.
_Avoid_: envelope, packet, request

### Head sequence

The **event** sequence a subscription is caught up to when it starts. Items up to it are replay, later ones are live. A **frame** carries it, never an **event**: a thread's history is not a record of who caught up when.

### Capability

One thing an **instance**'s **contract server** can do, reported so a **hub** knows what it may ask of that instance without reading a version.

### Access token

The single secret the user presents to the **hub**'s **contract server**. An authenticated connection holds every **scope**, because a hub has one user. The token never enters an **instance**.

### Browser session

What a browser holds after it exchanges the **access token** at the **hub**'s login route. The browser carries it in the **session cookie**. The hub stores it, so a restart does not sign the user out. Signing out or rotating the access token ends it. Not a **session**, which is one run of the agent loop.
_Avoid_: login, web session

### Session cookie

The `HttpOnly` cookie that names a **browser session**. Page JavaScript never reads it; the browser sends it on the socket upgrade.
_Avoid_: auth cookie, login token

### Control token

The secret a **hub** presents to one **instance**'s **contract server**. Each instance has its own. It reaches that instance only. Presented back to the hub, it proves which instance is calling, and holds only the **scopes** the **instance registry** grants that instance.

### Update token

The second secret a **hub** holds, for CI. It runs an **instance update** and reads its **lifecycle operation**, and every other hub method refuses it. Rotating it leaves the **access token** alone.
_Avoid_: CI token, deploy token

### Signal alias

The **hub**'s record of which managed **instance** answers the public **signal** path, so a webhook registered before the hub existed keeps its URL after adoption. At most one instance holds it.

### Instance template

The initial behavior, configuration and **setup fields** a **factory** supplies for one **factory role**, naming one of the image recipes kinby ships, with the **suggested picks** for that role. The **hub** copies its files into an instance that picks the role and records what it copied. The **manifest** it merges belongs to the instance from then on. An **instance update** brings the other files up to the factory's, except any the instance changed.

### Factory role

The part an **instance** plays in a **factory**, such as `coder`. A factory has an **instance template** per role. An instance picks at most one role, at creation, and keeps it for life.
_Avoid_: member, slot

### Vanilla instance

An **instance** that picked no **factory role**, created from kinby's built-in defaults, such as the home instance.

### Library

The **hub**'s store of what an **instance** can pick: **skills** and **factories**. kinby ships a default library, and the user adds to it, edits it and removes from it. Nothing in it reaches an instance until picked.
_Avoid_: registry, marketplace, skill repository, package list

### Library entry

One pickable thing in the **library**, of one kind. An entry added from git records the repository, path and commit it came from, and moves to another commit only when the user moves it.

### Pick

An **instance**'s choice of a **library entry**. Picking a **skill** copies it into the instance as an instance-tier skill and records the version copied, and a running instance can add or drop one. An **instance update** brings a picked skill up to the library's unless the instance changed it. Picking a **factory role** happens once, at creation.
_Avoid_: install (of a skill or a factory), package

### Suggested pick

A **skill** a **factory role** proposes to an **instance** that picks the role. The **onboarding** preselects it, and the user may decline it.

### Onboarding

The one flow that creates an **instance**: its name and appearance, image, **factory role**, **skills**, prompt, and the setup those **picks** require. The web app and the home instance both run it.
_Avoid_: factory install, create wizard

### Plugin

Anything an **instance** loads beyond the core: a **tool**, a **skill** or a **hook**, from the instance directory or from kinby's defaults. The workspace never supplies plugins.

### Tool

A native Python capability the model can call during a **turn**. Every tool declares whether it writes (changes files, state, or the outside world); the **gate** reads that flag. Tools present at the start of a turn are the tools for that turn.

### Default tools

The **tools** kinby supplies to every instance unless its **manifest** disables them: read, write, edit, grep, glob and bash.

### Tool registry

The instance-owned collection of tools available to turns. It keeps the last valid set when a tool file cannot load.

### Tool snapshot

The fixed, name-sorted set of tools for one **turn**. The model and tool calls use the same snapshot for the whole turn.

### Skill

A markdown instruction set the model reads on demand. Skills are listed to the model by name and description; the body is fetched only when the model asks for it.

### Skill tier

Where a **skill** comes from: the **instance**, kinby itself, or the **workspace**, in that order of precedence. A skill hides any skill of the same name in a lower tier. Customizing a skill copies it into the instance tier.
_Avoid_: skill level, skill source

### Skill tool

The core, read-only **tool** that returns an available **skill** body by name. It is present on every **turn**.

### Instance tool

A core write **tool** through which the agent changes its own **instance**, such as a **routine** or a **skill**, under the **gate** like any other write. Workspace file tools never reach the instance.
_Avoid_: self-modification tool, config tool

### Instance code

The Python project an **instance** keeps for itself: the scripts it writes and runs, the packages they need, and the **authored tools** it exposes. The agent changes it only through **instance tools**, and it survives restarts and updates. The user's own tools are not part of it.
_Avoid_: sandbox code, scratch scripts

### Authored tool

A **tool** the agent wrote in its **instance code** and exposed to its own **turns**. The **gate** treats every call to it as a write, whatever it declares, until the user marks it read-only. The agent may change or remove only the tools it authored.
_Avoid_: dynamic tool, self-made tool

### MCP server

A **library** kind: an outside server whose tools an **instance** that picks it gets on its **turns**. The user or the **home instance** adds it to the library; an instance never adds one to itself. Every call to its tools is gated as a write unless the user trusts the server's own read-only hints.

### Routine grant

The user's standing allow for one **tool** inside one **routine**, given from an **approval** raised in that routine's **turn**. Any edit to the routine, by the user or the agent, revokes it.
_Avoid_: standing approval, always-allow

### Browser service

The one browser the **hub** runs for every **instance** that has the browser turned on. An instance asks the hub for a browser for one **turn**, and every turn gets a fresh one that shares nothing with another turn or instance. The browser reaches the internet only, never the hub, another instance or the user's own network.

### Browser profile

A named signed-in state an **instance** can open a page with, such as its LinkedIn session. It is kept with the **browser service**, never in the instance, so the agent names a profile but never reads it. The user creates and removes profiles.

### Vault

An **instance**'s sign-in credentials, each bound to one site, which the **browser service** types into that site's page for the agent. The user adds entries; the agent sees only their names and sites, never their values. Unlike a **hub secret**, a vault entry never reaches the instance.

### Takeover

The user driving an **instance**'s browser live from the web app, to sign in or get past a check the agent cannot. The agent asks for one and its **turn** waits, needing the user, until the user finishes; the user can also start one to create a **browser profile**.
_Avoid_: handoff, which the factory uses for work handed to it

### View

A piece of UI that belongs to one **instance** and that its **turns** and **routines** keep current, such as a board of job applications a daily routine updates. The agent creates, reshapes and fills a view whenever it judges a view shows something more precisely than a message would. A view is a layout composed from the **view catalogue** plus the data it shows, and it outlives any **thread**. The user reads it; in v1 the user changes it only by asking the agent.
_Avoid_: artifact, widget, dashboard, surface

### View catalogue

The fixed set of components kinby ships from which every **view** is composed. A view can use only what the catalogue holds, so no view carries code of its own; the catalogue grows only with a kinby release.
_Avoid_: component library

### Hub tool

A **tool** only the **home instance** holds, through which it reads the **hub** and other **instances**, or asks the hub to change something the hub owns or another instance's setup. One that writes asks in every **permission mode**, and the hub runs it only after seeing the user approve that exact call. Hub tools are present only on turns the user starts.
_Avoid_: admin tool, system tool

### Behavior prompt

The instance's own instructions to the model (`SYSTEM.md`). One of the sources assembled into the system prompt, alongside the **profile**, workspace **conventions**, the skill list, and the harness-owned environment block.

### System prompt

The single system message assembled for each **turn** from an ordered set of **prompt sections**.

### Prompt section

One named, attributable part of the **system prompt**. Missing file-backed sections are omitted.

### Preamble

The constant, harness-owned **prompt section** that introduces the teammate and the kinby software.

### Skills catalogue

The **prompt section** that lists each available **skill** by name and description. The model reads a skill's body on demand.

### Environment block

The last **prompt section**, containing the instance id, optional **persona name**, **workspace** path, main model, and date.

### Wake

The start of a **turn** by anything other than the user typing. Every wake carries an **origin**, which the thread records and the prompt renders as a cue.

### Origin

What started a **turn**: the user, a **routine**, a **step** of a **factory run**, or the **home instance**. A routine turn also records its trigger: scheduled, manual, catch-up, or a **signal**'s delivery. A step's turn records its factory, run and step.

### Routine

A **wake** an **instance** owns: a prompt, and optionally the instance's own **code step** that runs first. Started by a schedule, by a **delivery**, or by hand. Lives as a directory in the instance; its file is the source of truth.
_Avoid_: cron job, automation, job

### Code step

A **routine**'s own deterministic code, a **tool** never offered to the model, that runs before the model and may report "nothing new", completing the **turn** with **no work**.

### Coding client

A command-line agent that implements or reviews repository changes for a coding **instance** under a separate model subscription.
_Avoid_: model, subagent

### Coding run

A **delegated run** of a **coding client**, identified by its client session. A later invocation can resume that session to repair the change.

### Delegated pipeline

The issue-to-PR process that assigns implementation and review to **coding clients**, then gives the coding **instance** a **pipeline report**.

### Factory

A declared sequence of **steps** the **hub** runs to carry a **work item** from start to finish, handing it between **instances** and deciding what happens when a step fails. Its instances are ordinary instances that picked one of its **factory roles**, and a **routine** in one of them hands it new work. It holds behavior only: nothing in it names one repository's commands or conventions, or a picked **skill**.
_Avoid_: workflow, package, pipeline (for the concept)

### Step

One stage of a **factory**: a task done by an **instance**'s turn, a **coding client** run inside an instance, or deterministic code, or a wait on a **signal** or on the user's approval. A wait or an approval parks the run, which holds no instance until the signal, a deadline or the user moves it on. It ends with a **step result**.

### Work item

The unit of work a **factory** carries, such as one issue.

### Factory run

One **work item**'s trip through a **factory**'s **steps**. It belongs to the **instance** that handed the work item in. The **hub** keeps it between steps: the current step, the **step results** so far, and the attempts per step.

### Run worktree

The checkout of the **workspace** that one **factory run** works in, on that run's branch. Its run's first step cuts it from the base, and it lives until the run ends done or cancelled: a **stop** for a human, a retry and a park leave it as it is, and sending the run back to that first step cuts it again. No other run's steps touch it.
_Avoid_: workspace (the shared clone run worktrees are cut from), worktree (git's mechanism, not the concept)

### Run usage

The derived record of one **factory run**'s cost: its tokens and price across its **steps**' **turns**, **delegated runs** and recap, the **step attempts** per step, its retries and send-backs, and the time from start to done. Nothing stores it; it is read from the **hub** and the **instance**.

### Step result

What a **step** hands on: a short summary plus the named values the **factory** declares, recorded by the step's **hook** or produced by the step itself, never read from an agent's own report. The next step receives the **work item** with every result before it, and checks the values it requires before it starts; a missing one fails the step that should have produced it.

### Step attempt

One try at a **step** of a **factory run**: when it started and ended, how it ended, and its **step result**. It ends clean, failed, timed out, or needing a human when the step lacks what only the user can supply, such as **repository commands** its instance does not hold. A call the instance provably did not take, because the hub could not send it or the instance was draining, is no attempt: the hub offers the step again until the instance takes it. Neither is one the **hub** stopped during, or whose answer a lost connection kept from the hub: the instance, not the connection, says how it ended, and a call the instance does not hold is offered again. One the instance's own restart cut short ends failed. An attempt the subscription's limit stopped ends limited: the run parks until the **plan window** resets, then tries the step again without spending a retry. A failed attempt is tried again under the step's retry; a timed-out one, or one needing a human, never is and is never sent back. A step that declares where its failures go sends a failed attempt back there instead, with what failed. After that the run is **needs human**.

### Hook

Deterministic **instance** code, never offered to the model, that kinby runs when a **step** ends, however it ended, to record the **step result** from the world: the repository, an outside system, files the step wrote. kinby ships default hooks, and an instance can hold its own.
_Avoid_: callback, evidence check

### Intake

The **routine** that starts **factory runs**: it finds new **work items** and hands each to its **factory**.

### Needs human

A **factory run** stopped for the user because a **step** ran out of retries or send-backs, lacked what only the user can supply, or because the run's final check failed. Nothing it did is undone. The **factory** can say so where the **work item** came from, as the **software factory** labels its issue `ready-for-human`. Its work stays where it is until the run ends. The user retries the step, sends the run back, or cancels it; handing the same **work item** in again retries it. A run whose work finished elsewhere ends without the user: done when its change was merged, cancelled when its work item or change was closed.
_Avoid_: stopped run (a **stop** also ends a run as done or cancelled), restart

### Run stop

A **factory run** moving to done, cancelled or **needs human**. The **hub** tells the **instance** that handed the run in about each stop once, with the **step attempts** since the run's previous stop. A run that is retried or sent back after needing a human can stop again. Once a run ends done or cancelled, the **factory** can act on it once, as the **software factory** says on the issue why a run made no change.

### Factory check

kinby's validation of a **factory** as a whole, its file, its **instance templates**, and the **tools** and **hooks** they name, before anything changes. A broken factory keeps its last good version.

### Software factory

The **factory** kinby ships that turns **eligible issues** into **agent PRs**. A starting point the user may edit or delete.

### Scan

The **software factory**'s search for the oldest **eligible issue** after a **wake**. A **delivery** can determine that no scan is needed, but never selects the issue.

### Review round

One independent review of the current change against both the repository's standards and its ticket, followed by fixes when the review has hard findings.

### Fix round

One **babysitting** pass that asks the **coding client** to address every **actionable thread** trusted authors wrote, runs the repository checks, pushes the commit, and posts each **review reply**. A thread with any other author starts no fix round: it needs a human.

### Check repair

The **coding client**, resumed in the session that implemented the change, fixing what a failed repository check reported, or a check that failed on its pull request's head. The **software factory** sends a failed check back for repair a few times before the run is **needs human**.
_Avoid_: fix round (that answers review threads)

### Repository commands

The commands an **instance** runs for the repository it works in, held as named lists, such as the setup commands that prepare a checkout and the check commands that judge a change. A **factory**'s steps name the lists they run, and an empty list is a valid one. The instance records them through the **instance tool**. They belong to the instance, never to the **factory** or the repository, so two instances of one **factory role** each hold their own.
_Avoid_: onboarding, environment, the repository's gate

### Repository survey

An **instance** reading its repository to propose its **repository commands**, when the user asks for it. It proposes commands only, and reports a toolchain or service the image lacks as text.
_Avoid_: onboarding, environment setup

### Babysitting

The **software factory** watching its **agent PRs** after they open, answering review feedback and stopping when each pull request is **merge-ready**, merged, closed, or needs a human.

### Review thread

One conversation attached to a pull request diff. It may be resolved, or remain open for more replies.

### Review reply

The coding **instance**'s answer on a **review thread**. A reply records whether the feedback was fixed and explains what changed or why it stayed unchanged. A fixed reply names the commit that holds the fix.

### Actionable thread

An unresolved **review thread** whose latest reply is not from the coding **instance**. The **software factory** has feedback to handle there.

### Merge-ready

An **agent PR** with no **actionable threads** and no check whose newest run on its current head is still running or failed, that a trusted reviewer reviewed on that head. A trusted review app's successful check run on the head is a review of it. A pull request no reviewer answered within **babysitting**'s quiet time is merge-ready too, and a comment on it says so. The label reports readiness; the **software factory** never merges it.

### Agent PR

A pull request that a **delegated pipeline** opens for one **eligible issue**. Its branch name starts with `agent/`.
_Avoid_: automated PR, bot PR

### Stack

A parent issue's ordered series of **agent PRs**, each based on the PR below it. A top-level issue forms a stack of one.
_Avoid_: issue tree, branch chain

### Eligible issue

An open issue marked `ready-for-agent` that has no **agent PR** and whose open blockers already have an agent PR in the same **stack**.

The label counts only on the issue itself. A parent's `ready-for-agent` doesn't make its sub-issues eligible, so each sub-issue in a **stack** carries its own label, and its blockers decide the order.

### Pipeline report

The structured result of one **delegated pipeline** run. It identifies the issue and outcome, the PR when opened, review findings, checks, client usage, durations, and any failure.

### No work

A completed **turn** whose **code step** found nothing for the model to do. Neither the main model nor the **recap** model runs.

### Signal

An inbound stimulus from outside the **instance**, such as a webhook call or a received email, that wakes the agent without a schedule. Never called an event: that word names a thread history record.
_Avoid_: event, trigger, webhook (as the concept)

### Receiver

The part of **serve mode** that listens for **signals**, authenticates each call against the **routine** it names, and records the accepted ones as **deliveries**. A rejected call leaves no record.

### Delivery

One authenticated inbound call the **receiver** accepted for a **routine**. Recorded the moment it arrives, before any work starts, so it survives a restart. The **scheduler** starts its **turn** when the instance is free and the routine is enabled. Disabling the routine leaves queued signals pending until re-enabled. Two deliveries with the same provider id are one delivery.

### Delivery receipt

The **scheduler** result returned when the **receiver** records a **delivery**. It carries the accepted thread, turn and event sequence, plus whether the provider id matched an existing delivery.

### Scheduler

The instance's coordinator of **routine** wakes, whether due by schedule or waiting as a **delivery**.

### Firing

One attempt to start a **routine** turn, whether scheduled, caught up after downtime, requested manually, or started by a **delivery**.

### Routine payload

The body text and inferred content type read from a file passed to `kinby routine run --payload`.

### Armed routine

An enabled scheduled **routine** with its next firing time selected.

### Routine notice

A durable message about a **routine**'s first failure in a streak or its automatic disablement.

### Failure policy

The rule that counts consecutive failed **firings**, reports the first failure, and disables a **routine** at ten failures. Successful work resets the count; no work, interruption, and daily-budget refusal leave it unchanged.

### Failure reset

The **scheduler**'s record that a **routine** the **failure policy** disabled is enabled again, whoever enabled it. It goes on the turn of the failed **firing** that caused the disable, once, and the routine's next failure is a first failure. The disable notice stays.

### Cron schedule

A five-field recurrence rule for a **routine**, interpreted in the instance's time zone.
