# The playground box

The deployed hub, its instances and its workbenches run on the playground box (netcup, Debian 13), served at `kinby.jorgesolerrr.dev`. Read this before you debug any of them. Paths were checked on the box on 2026-11-15.

## Reaching it

- The ssh alias is `playground` (user `jorge`, in `~/.ssh/config`). `playground-root` is the same box as root. Use it only when a file is root-owned.
- Call it non-interactively from Git Bash, with single quotes around the remote command: `ssh -o BatchMode=yes -o ConnectTimeout=10 playground '<cmd>'`.
- **At most 5 ssh connections in any 30 seconds.** `ufw limit` on port 22 drops a sixth, for both aliases, and each dropped attempt restarts the 30 seconds. So put everything you need into one ssh call: chain the remote commands with `;` inside the single quotes. Each `hub.sh` call is one connection, so never loop over it; run the loop on the box in one ssh call instead.
- If ssh times out while `https://kinby.jorgesolerrr.dev` still answers, you hit that limit. Stop every ssh attempt, including background retry loops, wait a full minute, then connect once. Retrying sooner keeps the block alive. fail2ban is not the cause: it bans failed logins only (`sudo fail2ban-client status sshd`).
- The host has no `sqlite3`. Query sqlite through `python3` on the host or `python` in a container.

## What runs where

| Thing | Where |
|---|---|
| Checkout of `jorgesolerrr/kinby` | `/home/jorge/dev/kinby`. Cron runs `git fetch` every minute but never merges. |
| Hub container | `kinby-hub-1`, from `compose.hub.yaml`. Mounts `/home/jorge/kinby-hub` at `/hub` and the checkout at `/source`. |
| Caddy | `kinby-caddy-1` |
| Instances and workbenches | One container each, named after its ID, a UUID. Data is in `/home/jorge/kinby-hub/instances/<id>`, mounted at `/instance`, for workbenches too. Files there are root-owned. `hub.sh instances` maps each instance ID to its name and lists no workbench. The workbenches query below lists those. |
| Instances | home (the home instance), Finance, Reading, and CEC management. course-builder is the old course instance, which runs kinby's base image and could not be converted. |
| Lines | Each factory's runs happen on a line: a workbench, and the instance that owns the line and holds its intake routine. |

The lines on the box:

| Line | Factory | Workbench | Owner | Work |
|---|---|---|---|---|
| `225a81a3` | `software` | `software-coder` (`34c185a9`) | home | `jorgesolerrr/kinby` issues |
| `70d7b773` | `software` | `cec-backend` (`6d76e4f0`) | CEC management | `pb-cec/CEC-Backend` issues |
| `858bf3d8` | `software` | `landing-coder` (`0e4f9dd5`) | CEC management | `pb-cec/PB-CEC-LandingPage` issues |
| `e43399a8` | `reading-brief` | `reading-brief` (`8c430397`) | Reading | Titles Reading hands in |
| `0546ed6c` | `course` | `course` (`9d479f90`) | home | Courses home hands in |

The three `software` workbenches were converted from the coder instances of the same names, so they keep those instances' IDs. Each repository's webhook still posts to `/instances/<workbench id>/signals/scan`, and the hub hands each delivery to the line's intake in its owner.

| Thing | Where |
|---|---|
| CEC-Agent's test services | `cec-agent-test-postgres` and `cec-agent-test-minio`, from `/home/jorge/cec-agent-kinby/compose.yaml` |
| Hub registry | `/home/jorge/kinby-hub/registry.sqlite`, which is `/hub/registry.sqlite` in the hub. `workbenches` and `lines` hold the lines above. |
| Hub access token | `/home/jorge/kinby-hub/access-token`. Pipe it and never print it. See `.claude/skills/e2e-pass/SKILL.md`. |
| Instance events | `/home/jorge/kinby-hub/instances/<id>/.state/instance.sqlite`. The box has no `sqlite3`: `docker exec <id> kinby thread show <thread-id> /instance` prints one thread's events, or a factory run's events by the run's id. |
| History before instance.sqlite | Each instance's JSONL stores, checkpoints and database as they were before the #1200 migration, in `/home/jorge/kinby-hub/backups/migration-1200/<id>.tgz`. software-coder also keeps `events-archive-2026-11-06.jsonl` in its `.state`. |

## Ready queries

Each line below is one ssh connection. Join the ones you need into a single call.

```sh
# Containers and health
ssh -o BatchMode=yes playground 'docker ps --format "{{.Names}}\t{{.Status}}"'

# Instance IDs and names. Every <id> below is one of these IDs, never a name.
bash .claude/skills/e2e-pass/hub.sh instances

# Logs (hub, then an instance by its ID). Add -t for timestamps: a frame line has none of its own.
ssh -o BatchMode=yes playground 'docker logs --since 30m kinby-hub-1 2>&1 | tail -40'
ssh -o BatchMode=yes playground 'docker logs --since 30m <id> 2>&1 | grep -v "GET /health" | tail -40'

# Registry: tables are instances, operations, operation_steps, sessions, logins, ...
ssh -o BatchMode=yes playground 'docker exec kinby-hub-1 python -c "import sqlite3; c=sqlite3.connect(\"/hub/registry.sqlite\"); [print(r) for r in c.execute(\"select id, persona_name, intended_state, runtime_id from instances\")]; [print(r) for r in c.execute(\"select instance_id, kind, state, detail from operations order by rowid desc limit 5\")]"'

# Workbenches and lines
ssh -o BatchMode=yes playground 'docker exec kinby-hub-1 python -c "import sqlite3; c=sqlite3.connect(\"/hub/registry.sqlite\"); [print(r) for r in c.execute(\"select instance_id, recipe, workspace_source from workbenches\")]; [print(r) for r in c.execute(\"select id, factory, workbench_id, owner_id, intake from lines\")]"'

# Factory runs and where each stopped. step_attempts holds every step's ending, summary and times by run_id.
ssh -o BatchMode=yes playground 'docker exec kinby-hub-1 python -c "import sqlite3; c=sqlite3.connect(\"/hub/registry.sqlite\"); [print(r) for r in c.execute(\"select id, line_id, factory, work_item, status, step, updated_at from factory_runs order by rowid desc limit 10\")]"'

# What a workbench is working on, by the workbench's ID
ssh -o BatchMode=yes playground 'docker exec <id> git -C /instance/workspace worktree list; docker top <id> -eo pid,etimes,args | cut -c1-160'
```

## Runs that need a human

A run that stopped stays unfinished in `needs-human`. Putting `ready-for-agent` back on its issue starts nothing while that run exists, because the hub hands the scan the same run again. Restart the run through the hub instead, by its run id or its issue number. `hub.sh retry 825` tries the step it stopped at again, and `hub.sh send-back 825 prepare` sends it to an earlier step. `hub.sh cancel 825` ends it, and then the label starts a new run, unless the issue already has an open agent pull request.

Each `software` run works in its own run worktree on its workbench, `/instance/worktrees/<branch>`, and the workspace stays on the default branch. A run that stops keeps its run worktree as it left it, uncommitted work included, until the run ends. Sending it back to `prepare` starts it over from the base in a fresh one.

## Run transcripts

Nothing saves a run's stdout by default. Claude Code keeps each session of a workbench in `/instance/.state/claude/projects/`, in the workbench's directory, so sessions survive a recreate of the container. Each run's step transcripts are in `/instance/transcripts/<run id>`. Sessions from before #635 were in `/root/.claude/projects/` and went with the container. What a run teaches is remembered by its line's owner, not by the workbench.

## Changing the box

Redeploying, restarting containers and editing files under `kinby-hub/` are writes. Do them only when Jorge asks. The hub is redeployed by `.claude/skills/e2e-pass/deploy.sh`, which also updates the instances.

Lines are changed through the hub's contract, run inside the hub container with the access token: `line.setup`, `line.owner.set`, `instance.convert` and the `workbench.*` methods. The web app's Factories area sets lines up too.
