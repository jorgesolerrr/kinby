---
title: Decide the routines model (the first-class primitive)
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [05-research-claude-agent-sdk.md]
---

## Question

Routines are the project's differentiator and the mechanism of teammate proactivity. Spec the v0.1 primitive: (1) the routine object — trigger (cron and which event types at launch?) + prompt + destination + per-routine autonomy level (approval-first default already decided) — and how it's stored/versioned (memory graph? files? both?); (2) creation UX — conversational ("every weekday at 9, triage my inbox") parsed into the object, plus direct editing; (3) execution semantics — headless agent session per run? what context does a run get (memory access, prior-run awareness), where do results/approvals surface in the web chat; (4) missed-run policy when the host was down (catch up, skip, notify); (5) failure/cost guardrails (max runs, budget caps, kill switch); (6) the seam that the future teachable-routines ambition (agent proposes routines) will plug into, without designing it now. Output: routines spec section for the blueprint. Invoke /domain-modeling — this vocabulary (routine, run, trigger, destination, autonomy) becomes the project's ubiquitous language.
