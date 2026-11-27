---
date: 2026-09-10
thread: 5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c02
turn: 6b7c8d9e-0f1a-4b2c-9d3e-4f5a6b7c8d02
description: "Loaded STAGING_KEY with vault-load staging and deployed the blog"
subjects: ["staging deploy", "STAGING_KEY", "blog"]
tools: ["bash"]
---
## What happened
The turn ran `vault-load staging`, which exports STAGING_KEY, then `make deploy-staging`. The blog reached staging.
