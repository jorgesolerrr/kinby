---
date: 2026-09-25
thread: 9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e61
description: "make deploy-staging refuses to start without STAGING_KEY"
subjects: ["staging deploy", "STAGING_KEY", "blog"]
sources: ["2026-09-03-01a21bc422e170a4a5d97f7b00366cbd-the-blog-s-staging-deploy-failed-because-staging-key-was-not-set", "2026-09-24-01a21bc422e170a4a5d97f7dbaabae3a-the-newsletter-s-staging-deploy-stopped-on-a-missing-staging-key"]
---
`make deploy-staging` checks STAGING_KEY and stops when it is unset. `vault-load staging` sets it.

## History
The blog failed this way on 2026-09-03 and the newsletter on 2026-09-24.
