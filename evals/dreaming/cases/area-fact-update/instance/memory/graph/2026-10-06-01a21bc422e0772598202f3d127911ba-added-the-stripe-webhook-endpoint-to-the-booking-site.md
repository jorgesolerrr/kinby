---
date: 2026-10-06
run: 1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e51
generation: 1
workbench: "booking-site"
description: "Added the Stripe webhook endpoint to the booking site"
subjects: ["stripe webhook", "booking site"]
tools: ["bash"]
---
## What happened
The implement step added `POST /webhooks/stripe` in `api/webhooks/stripe.py`. It handles `checkout.session.completed` and marks the booking paid.
