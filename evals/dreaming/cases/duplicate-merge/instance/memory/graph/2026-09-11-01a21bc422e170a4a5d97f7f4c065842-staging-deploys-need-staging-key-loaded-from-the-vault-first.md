---
date: 2026-09-11
thread: 9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e61
description: "Staging deploys need STAGING_KEY loaded from the vault first"
subjects: ["staging deploy", "STAGING_KEY", "blog"]
sources: ["2026-09-03-01a21bc422e170a4a5d97f7b00366cbd-the-blog-s-staging-deploy-failed-because-staging-key-was-not-set", "2026-09-10-01a21bc422e170a4a5d97f7c77060048-loaded-staging-key-with-vault-load-staging-and-deployed-the-blog"]
---
Before `make deploy-staging`, run `vault-load staging` so STAGING_KEY is set.

## History
On 2026-09-03 the blog's deploy failed without the key. On 2026-09-10 the turn loaded it with `vault-load staging` and the deploy went through.
