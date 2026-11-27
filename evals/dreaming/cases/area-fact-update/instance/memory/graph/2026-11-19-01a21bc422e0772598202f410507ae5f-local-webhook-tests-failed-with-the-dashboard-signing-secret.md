---
date: 2026-11-19
run: 4f5a6b7c-8d9e-4f0a-1b2c-3d4e5f6a7b81
generation: 1
workbench: "booking-site"
description: "Local webhook tests failed with the dashboard signing secret"
subjects: ["stripe webhook", "booking site"]
tools: ["bash"]
---
## What happened
The webhook tests run against `stripe listen --forward-to localhost:8000/webhooks/stripe`. They failed signature checks because `STRIPE_WEBHOOK_SECRET` held the dashboard endpoint's secret. `stripe listen` prints its own `whsec_` secret, and the tests pass once `STRIPE_WEBHOOK_SECRET` is set to it.
