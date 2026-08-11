---
title: Create the GitHub repository
labels: [wayfinder:task]
status: closed
assignee: jorgesolerrr
blocked-by: [01-project-name.md]
---

## Question

Task (AFK where `gh` auth allows, else a checklist for the user): create the public repo under the user's personal GitHub account with the chosen name, Apache-2.0 license, initial README stating the project's thesis (self-hosted personal AI teammate: graph memory, first-class routines, web-first), and `git init` + first push from this working directory (bringing `research/` and `wayfinder/` along as project history). Record the repo URL in the resolution — later tickets and the blueprint live there.

## Resolution

Done AFK on 2026-08-10 (`gh` was authenticated as **jorgesolerrr**).

- **Repo:** https://github.com/jorgesolerrr/kinby — public, on the user's personal account, description "Open-source, self-hosted personal AI teammate - graph memory, first-class routines, web-first."
- **License:** Apache-2.0, canonical text committed as `LICENSE`.
- **README:** thesis committed — self-hosted personal AI teammate; graph memory, first-class routines, web-first, Claude-first/MCP, Docker Compose reference deployment; status section points at `wayfinder/` and `research/`.
- **History:** `git init -b main` in this working directory; first commit `5273e46` brought `wayfinder/` (map + 12 tickets), `research/` (5 assets), and `CONTEXT.md` along; pushed to `origin/main`.

Facts later tickets depend on: the tracker (`wayfinder/`) and research assets now live in the repo — resolutions should be committed and pushed as they land. Remote is `origin` over HTTPS.
