---
date: 2026-11-17
run: 3e4f5a6b-7c8d-4e9f-0a1b-2c3d4e5f6a71
generation: 1
workbench: "booking-site"
description: "Stripe retried a webhook and the booking site confirmed a booking twice"
subjects: ["stripe webhook", "booking site"]
tools: ["bash"]
---
## What happened
Stripe retried `checkout.session.completed` after a slow response and the handler sent a second confirmation email. Jorge decided the handler must be idempotent: it now records each Stripe event id in the `processed_events` table and skips an event it has seen.
## What should have gone differently
The handler should have deduplicated on the event id from the start.
