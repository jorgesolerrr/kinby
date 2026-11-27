---
date: 2026-11-18
thread: 5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c05
turn: 6b7c8d9e-0f1a-4b2c-9d3e-4f5a6b7c8d05
description: "Deployed the blog to staging; vault-load staging came first"
subjects: ["staging deploy", "STAGING_KEY", "blog"]
tools: ["bash"]
---
## What happened
Jorge asked for the blog on staging. The turn ran `vault-load staging`, then `make deploy-staging`, and the deploy went through.
