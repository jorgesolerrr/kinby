---
date: 2026-09-24
thread: 5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c03
turn: 6b7c8d9e-0f1a-4b2c-9d3e-4f5a6b7c8d03
description: "The newsletter's staging deploy stopped on a missing STAGING_KEY"
subjects: ["staging deploy", "STAGING_KEY", "newsletter"]
tools: ["bash"]
---
## What happened
`make deploy-staging` for the newsletter refused to start: STAGING_KEY was unset. Running `vault-load staging` first fixed it.
