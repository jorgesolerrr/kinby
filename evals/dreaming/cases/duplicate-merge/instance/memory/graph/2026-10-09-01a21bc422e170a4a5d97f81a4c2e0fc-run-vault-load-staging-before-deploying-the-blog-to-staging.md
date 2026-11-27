---
date: 2026-10-09
thread: 9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e61
description: "Run vault-load staging before deploying the blog to staging"
subjects: ["staging deploy", "STAGING_KEY", "blog"]
sources: ["2026-09-10-01a21bc422e170a4a5d97f7c77060048-loaded-staging-key-with-vault-load-staging-and-deployed-the-blog", "2026-10-08-01a21bc422e170a4a5d97f7eea62827b-deployed-the-blog-to-staging-after-vault-load-staging"]
---
Run `vault-load staging`, then `make deploy-staging`. Without it STAGING_KEY is unset and the deploy fails.

## History
It worked this way on 2026-09-10 and again on 2026-10-08.
