# The playground box

The deployed hub and the coder run on the playground box (netcup, Debian 13), served at `kinby.jorgesolerrr.dev`. Read this before you debug either of them. Paths were checked on the box on 2026-10-05.

## Reaching it

- The ssh alias is `playground` (user `jorge`, in `~/.ssh/config`). `playground-root` is the same box as root. Use it only when a file is root-owned.
- Call it non-interactively from Git Bash, with single quotes around the remote command: `ssh -o BatchMode=yes -o ConnectTimeout=10 playground '<cmd>'`.
- The host has no `sqlite3`. Query sqlite through `python3` on the host or `python` in a container.

## What runs where

| Thing | Where |
|---|---|
| Checkout of `jorgesolerrr/kinby` | `/home/jorge/dev/kinby`. Cron runs `git fetch` every minute but never merges. |
| Hub container | `kinby-hub-1`, from `compose.hub.yaml`. Mounts `/home/jorge/kinby-hub` at `/hub` and the checkout at `/source`. |
| Caddy | `kinby-caddy-1` |
| Coder container | `kinby-coder`. The hub manages it. It mounts `/home/jorge/kinby-hub/coder` at `/instance`. The workspace is `/instance/workspace`. |
| Other instances | One container per instance, named after its hub instance ID. Data is in `/home/jorge/kinby-hub/instances/<id>`. |
| Hub registry | `/home/jorge/kinby-hub/registry.sqlite`, which is `/hub/registry.sqlite` in the hub |
| Hub access token | `/home/jorge/kinby-hub/access-token`. Pipe it and never print it. See `.claude/skills/e2e-pass/SKILL.md`. |
| Coder events | `/home/jorge/kinby-hub/coder/.state/events.jsonl`. An instance's events are in `instances/<id>/.state/events.jsonl`. |
| Factory package source | `../kinby-code-factory`, which is `jorgesolerrr/kinby-code-factory` |

## Ready queries

```sh
# Containers and health
ssh -o BatchMode=yes playground 'docker ps --format "{{.Names}}\t{{.Status}}"'

# Logs (hub, coder)
ssh -o BatchMode=yes playground 'docker logs --since 30m kinby-hub-1 2>&1 | tail -40'
ssh -o BatchMode=yes playground 'docker logs --since 30m kinby-coder 2>&1 | grep -v "GET /health" | tail -40'

# Coder events: count of the last 200 event types, then the last turn outcome
ssh -o BatchMode=yes playground 'f=~/kinby-hub/coder/.state/events.jsonl; tail -n 200 $f | grep -o "\"type\":\"[a-z._]*\"" | sort | uniq -c; grep "turn.completed\|turn.failed" $f | tail -1 | cut -c1-400'

# Registry: tables are instances, operations, operation_steps, sessions, logins, ...
ssh -o BatchMode=yes playground 'docker exec kinby-hub-1 python -c "import sqlite3; c=sqlite3.connect(\"/hub/registry.sqlite\"); [print(r) for r in c.execute(\"select id, persona_name, intended_state, runtime_id from instances\")]; [print(r) for r in c.execute(\"select instance_id, kind, state, detail from operations order by rowid desc limit 5\")]"'

# What the coder is working on
ssh -o BatchMode=yes playground 'docker exec kinby-coder git -C /instance/workspace branch --show-current; docker top kinby-coder -o pid,etime,args | grep -E "claude|codex" | cut -c1-160'
```

## Run transcripts

Nothing saves a run's stdout by default. Earlier sessions wrapped `/usr/local/bin/claude` in `kinby-coder` so that each run was copied to `/tmp/claude-runs/<time>-<pid>.jsonl`. The wrapper and the directory disappear when the container is recreated. Installing it changes the box, so ask Jorge first.

## Changing the box

Redeploying, restarting containers and editing files under `kinby-hub/` are writes. Do them only when Jorge asks. The hub is redeployed by `.claude/skills/e2e-pass/deploy.sh`. A coder release comes from a merge to `main` in `kinby-code-factory`.
