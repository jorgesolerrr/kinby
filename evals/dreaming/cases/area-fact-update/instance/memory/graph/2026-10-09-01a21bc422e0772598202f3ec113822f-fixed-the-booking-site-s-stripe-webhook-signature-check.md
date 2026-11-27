---
date: 2026-10-09
run: 2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f61
generation: 1
workbench: "booking-site"
description: "Fixed the booking site's Stripe webhook signature check"
subjects: ["stripe webhook", "booking site"]
tools: ["bash"]
---
## What happened
The handler parsed the JSON body before verifying the signature, so every event failed `stripe.Webhook.construct_event`. The fix verifies against the raw request body.
