---
date: 2026-09-21
description: "Staging deploys need STAGING_KEY loaded from the vault first"
subjects: ["staging deploy", "STAGING_KEY", "vault"]
---
Before `make deploy-staging`, run `vault-load staging` so STAGING_KEY is set.
