---
date: 2026-10-31
description: "Known CI flake: tests/test_factory_waits.py::test_a_run_parked_by_its_plan_window_wakes_at_the_reset_after_a_hub_restart times out at 5 s on loaded runners; rerun the failed job instead of changing the branch"
subjects: ["CI", "flaky test", "test_factory_waits", "check repair"]
---
The test has a real-clock race in the hub's wake timer: a 2-second plan window against a 5-second timeout. With four xdist workers on CI it can time out although the branch does not touch it, and it passes when rerun. A check-repair round that finds this failure confirms the branch does not touch the factory wake code and reruns the job itself with `gh run rerun <id> --failed`. It does not lengthen the window, which only makes the race rarer. A real fix needs a controllable clock and belongs in its own ticket.
