---
date: 2026-10-10
thread: 7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c41
workbench: "booking-site"
description: "The booking site's Stripe webhook lives in api/webhooks/stripe.py"
subjects: ["stripe webhook", "booking site"]
sources: ["2026-10-06-01a21bc422e0772598202f3d127911ba-added-the-stripe-webhook-endpoint-to-the-booking-site", "2026-10-09-01a21bc422e0772598202f3ec113822f-fixed-the-booking-site-s-stripe-webhook-signature-check"]
---
`POST /webhooks/stripe` in `api/webhooks/stripe.py` handles `checkout.session.completed` and marks the booking paid. Verify the signature against the raw request body, before parsing JSON.
