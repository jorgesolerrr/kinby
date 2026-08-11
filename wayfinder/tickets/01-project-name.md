---
title: Choose the project name
labels: [wayfinder:grilling]
status: closed
assignee: jorge
blocked-by: []
---

## Question

Settle the invented-word project name (direction already decided: invented/abstract word, not mythology or a human name; the agent *persona* name stays a per-instance config field). A candidate wins when it: reads well as a Python package and CLI command, has a free GitHub repo slug and PyPI name, has no obvious trademark collision in the agent/AI space, and evokes the teammate/routines identity. Produce a shortlist with availability checked, grill to a winner. Blocks repo creation.

## Resolution

**The project is named `kinby`** — an invented word, *kin* + *by*: kin nearby, at your side. One lowercase word used identically everywhere: GitHub slug, PyPI package, `import kinby`, and the `kinby` CLI command.

**Taste criteria settled during grilling** (they shaped generation): soft/warm over crisp/technical; lightly root-blended over pure-abstract; the **teammate/companion** facet leads (routines and memory are mechanisms, not the soul); one word, 2–3 syllables, ≤ 7 letters. An initial bilingual (Spanish/English) requirement was relaxed when the user steered to an Anglo register; kinby still reads cleanly in both.

**Availability, verified 2026-08-10:** PyPI `kinby` free (404 on the JSON API); no exact-name GitHub repos; no company, product, or trademark found under the name anywhere (nearest neighbors — Kin, KineMaster, Kinedu — are clearly distinct); `kinby.dev` and `kinby.ai` both unresolving (best-effort DNS probe, not WHOIS). **Reserve the PyPI name and domains early** — cheapest insurance; natural to fold into repo creation.

**Route walked:** three generated batches, each availability-checked by a sub-agent before grilling. Batch 1 (Latin/Spanish roots): *yanapo*, *sempero*, *copano* survived clean but none sang. Batch 2 (Old English roots): produced runner-up **sidekin** (sidekick × kin — clean, self-explanatory) and *hobkin*; *witan* blocked (PyPI taken by Witan Labs, an AI-agent-tooling startup). Batch 3 (modern startup register): produced kinby; notable kills — *kinso* (active AI-assistant startup), *akin* and *sidle* (PyPI taken + AI-space collisions), *teamo* (4+ existing software products). Final head-to-head sidekin vs. kinby: user chose kinby.
