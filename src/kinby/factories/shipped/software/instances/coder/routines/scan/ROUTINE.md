---
description: Hand the oldest issue labeled ready-for-agent to the software factory.
schedule: 0 * * * *
mode: full-access
run: scan_ready_issues
signal:
  auth: hmac-sha256
  secret: GITHUB_WEBHOOK_SECRET
  signature_header: X-Hub-Signature-256
  delivery_header: X-GitHub-Delivery
---
The scan hands the factory its work itself, so there is nothing for you to do. Stop.
