# The playground box

The deployed hub and the coders run on the playground box (netcup, Debian 13), served at `kinby.jorgesolerrr.dev`. Read this before you debug any of them. Paths were checked on the box on 2026-10-13.

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
| Instances | One container per instance, named after its hub instance ID, a UUID. Data is in `/home/jorge/kinby-hub/instances/<id>`, mounted at `/instance`. Files there are root-owned. `hub.sh instances` maps each ID to its name. |
| kinby's coder | The instance `hub.sh instances` lists as `software-coder`, from the shipped `software` factory. Its workspace is `/instance/workspace`, a Docker volume of its own. |
| CEC-Agent's coder | The instance listed as `cec-coder`, from the shipped `software` factory. It holds its own repository commands. |
| PB-CEC-LandingPage's coder | The instance listed as `landing-coder`, from the shipped `software` factory. |
| CEC-Agent's test services | `cec-agent-test-postgres` and `cec-agent-test-minio`, from `/home/jorge/cec-agent-kinby/compose.yaml` |
| Hub registry | `/home/jorge/kinby-hub/registry.sqlite`, which is `/hub/registry.sqlite` in the hub |
| Hub access token | `/home/jorge/kinby-hub/access-token`. Pipe it and never print it. See `.claude/skills/e2e-pass/SKILL.md`. |
| Instance events | `/home/jorge/kinby-hub/instances/<id>/.state/events.jsonl` |

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

# Factory runs and where each stopped. step_attempts holds every step's ending, summary and times by run_id.
ssh -o BatchMode=yes playground 'docker exec kinby-hub-1 python -c "import sqlite3; c=sqlite3.connect(\"/hub/registry.sqlite\"); [print(r) for r in c.execute(\"select id, factory, work_item, status, step, updated_at from factory_runs order by rowid desc limit 10\")]"'

# What a coder is working on
ssh -o BatchMode=yes playground 'docker exec <id> git -C /instance/workspace branch --show-current; docker top <id> -eo pid,etimes,args | cut -c1-160'
```

## Runs that need a human

A run that stopped stays unfinished in `needs-human`. Putting `ready-for-agent` back on its issue starts nothing while that run exists, because the hub hands the scan the same run again. Restart the run through the hub instead. `factory.run.retry` tries the step it stopped at again, and `factory.run.send_back` sends it to an earlier step. `hub.sh cancel <run-id>` ends it, and then the label starts a new run, unless the issue already has an open agent pull request.

The runs of one coder share its workspace. A run that stops with uncommitted work leaves it there, and the next run's coding step fails on it. Send the stopped run back to `prepare`, which cleans the workspace, before you retry any other.

## Run transcripts

Nothing saves a run's stdout by default. Claude Code keeps each session of a coder in `/instance/.state/claude/projects/`, in the instance directory, so sessions survive a recreate of the container. Sessions from before #635 were in `/root/.claude/projects/` and went with the container.

## Changing the box

Redeploying, restarting containers and editing files under `kinby-hub/` are writes. Do them only when Jorge asks. The hub is redeployed by `.claude/skills/e2e-pass/deploy.sh`, which also updates the instances.
